"""Flask blueprint using the site's existing login + CSRF. No shared site tables."""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
from pathlib import Path
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
try:
    from .state import Store
except ImportError:
    from state import Store

DEMO = {
    'positive':'DEMO/TEST: Спасибо, очень удобно пользоваться сервисом.',
    'neutral':'DEMO/TEST: Получила результат, пока изучаю возможности сервиса.',
    'negative':'DEMO/TEST: Сервис не работает, появляется ошибка. Я недовольна результатом.',
}


def init_reviews(app, options=None):
    config={
        'database_path':os.getenv('REVIEWS_DATABASE_PATH','data/reviews.sqlite3'),
        'worker_token':os.getenv('WORKER_API_TOKEN',''),
        'intake_enabled':os.getenv('REVIEWS_INTAKE_ENABLED','false').lower()=='true',
        'privacy_url':os.getenv('PRIVACY_POLICY_URL',''),
        'consent_version':os.getenv('CONSENT_VERSION',''),
        'production':os.getenv('ENVIRONMENT',os.getenv('APP_ENV','development'))=='production',
        'trusted_proxy_ips':os.getenv('REVIEWS_TRUSTED_PROXY_IPS','').split(','),
    }
    config.update(options or {})
    if config['production'] and (len(config['worker_token'])<43 or len(set(config['worker_token']))<16):
        raise RuntimeError('Strong review worker token is required')
    if config['intake_enabled'] and (not config['privacy_url'].startswith('https://') or not config['consent_version']):
        raise RuntimeError('Approved review privacy notice required')
    store=Store(config['database_path'])
    app.extensions['review_store']=store
    app.config['REVIEWS_GITHUB_URL']=os.getenv('REVIEWS_GITHUB_URL','')
    bp=Blueprint('review_monitor',__name__,template_folder=str(Path(__file__).parent/'templates'),static_folder='assets',static_url_path='/review-assets')

    def form_id():
        if 'review_request_id' not in session:
            session['review_request_id']=secrets.token_hex(16)
        return session['review_request_id']

    def public_reviews():
        return render_template('monitor/reviews.html',items=store.listing(public=True,limit=100),request_id=form_id(),intake=config['intake_enabled'],privacy_url=config['privacy_url'])

    @bp.post('/reviews')
    def submit_review():
        if not config['intake_enabled']:
            abort(503)
        if request.content_length and request.content_length>16384:
            abort(413)
        text=(request.form.get('text') or '').strip()
        rid=request.form.get('request_id','')
        if request.form.get('website') or rid!=session.get('review_request_id'):
            abort(400)
        if not 3<=len(text)<=3000 or request.form.get('consent_processing')!='yes':
            flash('Проверьте текст (3–3000 символов) и отдельное согласие на обработку.','warning')
            return redirect(url_for('reviews'))
        # Audited chain: visitor -> Traefik -> nginx -> Flask. Ignore any attacker prefix.
        address=request.remote_addr or 'unknown'
        hops=list(request.access_route)
        trusted=set(config['trusted_proxy_ips'])
        if address in trusted and len(hops)>=2 and hops[-1] in trusted:
            try:
                address=str(ipaddress.ip_address(hops[-2]))
            except ValueError:
                abort(400)
        key=hmac.new(app.secret_key.encode() if isinstance(app.secret_key,str) else app.secret_key,address.encode(),hashlib.sha256).hexdigest()
        if not store.allow_request(key):
            abort(429)
        store.submit(text,rid,config['consent_version'],request.form.get('consent_publication')=='yes')
        # Keep ID for duplicate POST; rotate when visitor explicitly starts another form.
        flash('Отзыв сохранён и отправлен на проверку. Публикация возможна только после модерации.','success')
        return redirect(url_for('reviews')+'?submitted=1')

    @bp.post('/reviews/new')
    def new_form():
        session.pop('review_request_id',None)
        return redirect(url_for('reviews'))

    @bp.get('/api/reviews')
    def api_reviews():
        token=request.headers.get('X-Worker-Token','')
        if not config['worker_token'] or not hmac.compare_digest(token,config['worker_token']):
            abort(401)
        after=request.args.get('after','0')
        if not after.isdigit() or len(after)>18:
            abort(400)
        items=store.listing(after=int(after))
        response=jsonify(items=items,next_after=items[-1]['id'] if items else None)
        response.headers['Cache-Control']='no-store'
        return response

    @bp.get('/admin/reviews')
    @login_required
    def admin_reviews():
        after=request.args.get('after','0')
        if not after.isdigit() or len(after)>18:
            abort(400)
        items=store.listing(after=int(after))
        return render_template('monitor/admin_list.html',items=items)

    @bp.get('/admin/reviews/<int:review_id>')
    @login_required
    def admin_review(review_id):
        review=store.get(review_id)
        if not review:
            abort(404)
        return render_template('monitor/admin_detail.html',review=review,analysis=json.loads(review['payload']) if review['payload'] else None)

    @bp.post('/admin/reviews/<int:review_id>')
    @login_required
    def moderate(review_id):
        action=request.form.get('action')
        text=(request.form.get('text') or '').strip() or None
        if text and len(text)>3000:
            abort(400)
        if action=='approve_reply' and text and len(text)>1200:
            abort(400)
        try:
            store.moderate(review_id,action,f'web:{current_user.get_id()}',text)
        except ValueError:
            abort(400)
        return redirect(url_for('review_monitor.admin_review',review_id=review_id))

    @bp.post('/admin/reviews/demo')
    @login_required
    def create_demo():
        kind=request.form.get('kind')
        if kind not in DEMO:
            abort(400)
        review_id=store.submit(DEMO[kind],secrets.token_hex(16),'demo-v1',demo=True)
        return redirect(url_for('review_monitor.admin_review',review_id=review_id))

    @bp.get('/review-monitoring-assistant')
    def case_page():
        return render_template('monitor/case.html')

    @bp.after_request
    def protect_admin(response):
        if request.path.startswith(('/admin/','/api/')):
            response.headers['Cache-Control']='no-store'
        return response

    app.register_blueprint(bp)
    # Preserve the site's endpoint name so all existing navigation remains valid.
    if 'reviews' in app.view_functions:
        app.view_functions['reviews']=public_reviews
    else:
        app.add_url_rule('/reviews','reviews',public_reviews)
    return store
