import asyncio
import logging
try:
    from .config import get_settings
    from .state import Store
    from .processor import analyze_review
    from .telegram_bot import send_notification
except ImportError:
    from config import get_settings
    from state import Store
    from processor import analyze_review
    from telegram_bot import send_notification

logger=logging.getLogger('worker')


async def process_once(store, settings, analyzer=analyze_review, sender=send_notification):
    count=0
    for _ in range(20):
        review=store.claim()
        if not review:
            break
        try:
            analysis,provider=await analyzer(review,settings,store)
            store.complete(review,analysis,provider,analysis.tone=='negative' or settings.notify_non_negative)
            count+=1
            logger.info('review=%s analyzed provider=%s',review['id'],provider)
        except Exception as exc:
            store.failed(review,type(exc).__name__)
            logger.warning('review=%s analysis failed type=%s',review['id'],type(exc).__name__)
        store.heartbeat()
    # Do not consume attempts while Telegram is deliberately unconfigured.
    if settings.telegram_bot_token.get_secret_value() and settings.chat_id:
        for _ in range(20):
            review_id=store.claim_notification()
            if review_id is None:
                break
            try:
                status,message_id,error,delay=await sender(store.get(review_id),settings)
            except Exception:
                status,message_id,error,delay='uncertain',None,'sender_interrupted',0
            store.notification_result(review_id,status,message_id,error,delay)
            logger.info('review=%s telegram=%s',review_id,status)
            store.heartbeat()
    store.heartbeat()
    return count


async def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(name)s %(levelname)s %(message)s')
    # httpx logs full URLs at INFO, including the Telegram token.
    for name in ('httpx','httpcore','openai'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        settings=get_settings()
    except Exception:
        raise SystemExit('Invalid worker configuration; secret values suppressed') from None
    store=Store(settings.database_path)
    while True:
        try:
            await process_once(store,settings)
        except Exception as exc:
            logger.error('worker iteration failed type=%s',type(exc).__name__)
        await asyncio.sleep(settings.worker_poll_interval)


if __name__=='__main__':
    asyncio.run(main())
