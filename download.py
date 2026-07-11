from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter()

PLAY_STORE = "https://play.google.com/store/apps/details?id=com.prendiax.app&pcampaignid=web_share"
APP_STORE = "https://apps.apple.com/mx/app/prendiax/id6757627630"
WEB_PAGE = "https://prendiax.com"

@router.get("/download")
async def download(request: Request):
    user_agent = request.headers.get("user-agent", "").lower()

    if "iphone" in user_agent or "ipad" in user_agent or "ipod" in user_agent:
        return RedirectResponse(url=APP_STORE, status_code=302)

    if "android" in user_agent:
        return RedirectResponse(url=PLAY_STORE, status_code=302)

    # Si entra desde Windows, Mac, Linux, etc.
    return RedirectResponse(url=WEB_PAGE, status_code=302)