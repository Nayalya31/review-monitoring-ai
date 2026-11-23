import re
import uuid
import pytest
from flask import Flask
from flask_login import LoginManager,UserMixin
from flask_wtf import CSRFProtect
from jinja2 import DictLoader,ChoiceLoader
from site_integration import init_reviews
from processor import fallback

@pytest.fixture
def app(tmp_path):
    app=Flask(__name__)
    app.config.update(TESTING=True,SECRET_KEY='synthetic-test-secret')
    CSRFProtect(app)
    login=LoginManager(app)
    class User(UserMixin): id='1'
    @login.user_loader
    def load(uid): return User() if uid=='1' else None
    for endpoint in ['contact','admin_requests']:
        app.add_url_rule('/'+endpoint,endpoint,lambda:'ok')
    app.jinja_loader=ChoiceLoader([app.jinja_loader,DictLoader({'base.html':'<!DOCTYPE html><title>{% block title %}{% endblock %}</title>{% block content %}{% endblock %}'})])
    init_reviews(app,{'database_path':str(tmp_path/'site.sqlite3'),'worker_token':'test-secret','intake_enabled':True,'privacy_url':'https://example.test/privacy','consent_version':'v1'})
    return app


def login(client):
    with client.session_transaction() as s:
        s['_user_id']='1';s['_fresh']=True


def fields(client,path='/reviews'):
    html=client.get(path).text
    return {name:re.search(f'name="{name}" value="([^"]+)"',html).group(1) for name in ['csrf_token','request_id']}


def csrf(client,path):
    return re.search('name="csrf_token" value="([^"]+)"',client.get(path).text).group(1)


def test_submission_validation_csrf_and_xss(app):
    c=app.test_client()
    assert c.post('/reviews',data={'text':'bad'}).status_code==400
    data=fields(c)
    data.update(text='<script>alert(1)</script> DEMO/TEST: не работает',consent_processing='yes',consent_publication='yes')
    assert c.post('/reviews',data=data).status_code==302
    assert c.post('/reviews',data=data).status_code==302
    assert len(app.extensions['review_store'].listing())==1
    assert 'alert(1)' not in c.get('/reviews').text
    assert c.get('/admin/reviews').status_code==401
    login(c)
    html=c.get('/admin/reviews/1').text
    assert '&lt;script&gt;' in html and '<script>alert' not in html


def test_api_wrong_token_and_pagination(app):
    c=app.test_client()
    assert c.get('/api/reviews').status_code==401
    assert c.get('/api/reviews',headers={'X-Worker-Token':'wrong'}).status_code==401
    good={'X-Worker-Token':'test-secret'}
    assert c.get('/api/reviews',headers=good).json=={'items':[],'next_after':None}
    assert c.get('/api/reviews?after=abc',headers=good).status_code==400


def test_admin_separate_publication_and_reply(app):
    s=app.extensions['review_store']
    rid=s.submit('DEMO/TEST отличный сервис',uuid.uuid4().hex,'v1',publication=True)
    r=s.claim();s.complete(r,fallback(r['text'],'test'),'test',False)
    c=app.test_client()
    assert c.post(f'/admin/reviews/{rid}',data={'action':'approve_reply'}).status_code in (400,401)
    login(c)
    token=csrf(c,f'/admin/reviews/{rid}')
    assert c.post(f'/admin/reviews/{rid}',data={'csrf_token':token,'action':'approve_reply','text':'Спасибо!'}).status_code==302
    assert s.listing(public=True)==[]
    assert c.post(f'/admin/reviews/{rid}',data={'csrf_token':token,'action':'publish','text':'DEMO/TEST отличный сервис'}).status_code==302
    assert s.listing(public=True)[0]['response']=='Спасибо!'
    c.post(f'/admin/reviews/{rid}',data={'csrf_token':token,'action':'revise'})
    assert s.listing(public=True)[0]['response'] is None


def test_no_publication_without_consent_or_demo(app):
    s=app.extensions['review_store']
    for demo,consent in [(False,False),(True,True)]:
        rid=s.submit(f'DEMO/TEST {demo}',uuid.uuid4().hex,'v1',publication=consent,demo=demo)
        with pytest.raises(ValueError):s.moderate(rid,'publish','web:1','test text')


def test_demo_endpoint_requires_login_and_fixed_input(app):
    c=app.test_client()
    assert c.get('/admin/reviews').status_code==401
    login(c)
    token=csrf(c,'/admin/reviews')
    assert c.post('/admin/reviews/demo',data={'csrf_token':token,'kind':'negative','text':'arbitrary personal data'}).status_code==302
    review=app.extensions['review_store'].get(1)
    assert review['is_demo']==1
    assert 'arbitrary' not in review['text']
