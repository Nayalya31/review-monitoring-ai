"""Read-only authenticated site API adapter, for diagnostics/future remote workers.
Production worker consumes the isolated database on the same host directly.
Public automatic create/update reply methods were removed intentionally.
"""
import httpx
try:
    from .models import RemoteReview
except ImportError:
    from models import RemoteReview


class ReviewSiteClient:
    def __init__(self, settings, transport=None):
        self.settings=settings
        self.transport=transport

    async def fetch_reviews(self, after=0):
        async with httpx.AsyncClient(timeout=15,transport=self.transport,follow_redirects=False) as client:
            response=await client.get(self.settings.target_site_url.rstrip('/')+'/api/reviews',params={'after':after},headers={'X-Worker-Token':self.settings.worker_api_token.get_secret_value()})
            response.raise_for_status()
            return [RemoteReview.model_validate(x) for x in response.json()['items']]
