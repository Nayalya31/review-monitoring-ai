"""Explicit synthetic integration check. No raw secrets/provider errors in output."""
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config import Settings
from site_integration import DEMO
from state import Store
from worker import process_once

async def main():
    for name in ('httpx','httpcore','openai'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    settings=Settings()
    settings.external_processing_authorized=True  # Fixed synthetic catalogue only.
    settings.telegram_include_text=True
    store=Store('data/live-smoke.sqlite3')
    for kind,text in DEMO.items():
        store.submit(text,uuid.uuid4().hex,'demo-v1',demo=True)
    await process_once(store,settings)
    report=[]
    for item in store.listing():
        r=store.get(item['id'])
        report.append({'id':r['id'],'provider':r['provider'],'tone':json.loads(r['payload'])['tone'] if r['payload'] else None,'draft_status':r['draft_status'],'telegram':r['notification_status'],'message_id':r['message_id']})
    Path('docs/LIVE_SMOKE.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
    if not all(r['provider']=='openai' for r in report):
        raise SystemExit('LLM integration not verified')
    if not any(r['tone']=='negative' and r['telegram']=='sent' for r in report):
        raise SystemExit('Telegram integration not verified')

if __name__=='__main__':
    try:
        asyncio.run(main())
    except Exception as exc:
        raise SystemExit('Live check failed: '+type(exc).__name__+'; details suppressed') from None
