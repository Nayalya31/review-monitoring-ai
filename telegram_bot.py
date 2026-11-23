import httpx
try:
    from .models import Analysis
    from .processor import redact
except ImportError:
    from models import Analysis
    from processor import redact

LABELS={'negative':'негативная','neutral':'нейтральная','positive':'положительная'}


def build_review_message(review, settings):
    a=Analysis.model_validate_json(review['payload'])
    heading='🚨 НОВЫЙ НЕГАТИВНЫЙ ОТЗЫВ' if a.tone=='negative' else 'Новый отзыв'
    parts=(["DEMO/TEST — синтетическая проверка системы"] if review['is_demo'] else []) + [heading, f"ID: {review['id']}", 'Источник: 24tali-ai.ru', f"Дата (UTC): {review['created_at']}", f"Тональность: {LABELS[a.tone]}"]
    if settings.telegram_include_text and (review['is_demo'] or review['safe_text']):
        safe=review['safe_text'] or review['text']
        parts += ['Текст: '+redact(safe)[:800], 'Причина: '+ '; '.join(a.reasons)[:500], 'Рекомендованный ответ: '+a.draft[:900]]
    else:
        parts += ['Текст и черновик доступны в защищённой панели.']
    parts += ['Статус: требует внимания администратора.',f"{settings.target_site_url.rstrip('/')}/admin/reviews/{review['id']}"]
    return '\n\n'.join(parts)[:3500]


async def send_notification(review, settings, transport=None):
    if not settings.telegram_bot_token.get_secret_value() or not settings.chat_id:
        return 'retry',None,'not_configured',300
    url=f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}/sendMessage"
    # URL button uses the existing admin login; never places a credential in the link.
    payload={'chat_id':settings.chat_id,'text':build_review_message(review,settings),
             'link_preview_options':{'is_disabled':True},
             'reply_markup':{'inline_keyboard':[[{'text':'Открыть отзыв','url':f"{settings.target_site_url.rstrip('/')}/admin/reviews/{review['id']}"}]]}}
    try:
        async with httpx.AsyncClient(timeout=15,transport=transport,follow_redirects=False) as client:
            response=await client.post(url,json=payload)
        if response.status_code==429:
            try:
                delay=int(response.json().get('parameters',{}).get('retry_after',60))
            except (ValueError,TypeError):
                delay=60
            return 'retry',None,'rate_limited',max(1,min(delay,86400))
        if response.status_code>=500:
            return 'uncertain',None,'telegram_server_error',0
        if response.status_code>=400:
            return 'failed',None,f'http_{response.status_code}',0
        data=response.json()
        if data.get('ok') and isinstance(data.get('result',{}).get('message_id'),int):
            return 'sent',data['result']['message_id'],None,0
        return 'uncertain',None,'invalid_response',0
    except (httpx.ConnectError,httpx.ConnectTimeout,httpx.PoolTimeout):
        return 'retry',None,'connection_failed',60
    except Exception:
        return 'uncertain',None,'delivery_unknown',0


def authorize_callback(payload, settings):
    """Reserved for a separately enabled Telegram callback receiver; not deployed."""
    user_id=payload.get('from',{}).get('id')
    chat=str(payload.get('message',{}).get('chat',{}).get('id',''))
    return isinstance(user_id,int) and user_id in settings.allowed_telegram_ids and chat==settings.chat_id
