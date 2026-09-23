"""FastAPI server for WhatsApp Cloud API Calling webhooks."""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional

import aiohttp
import uvicorn
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from loguru import logger
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.whatsapp.api import WhatsAppConnectCall, WhatsAppWebhookRequest
from pipecat.transports.whatsapp.client import WhatsAppClient

from lingo.bot import run_bot
from lingo.config import Settings

whatsapp_client: Optional[WhatsAppClient] = None
settings: Optional[Settings] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global whatsapp_client, settings

    # Load settings - only require AI credentials if in conversation mode
    bot_mode = os.getenv("BOT_MODE", "conversation").strip().lower()
    require_ai = (bot_mode == "conversation")
    settings = Settings.from_env(require_whatsapp=True, require_ai=require_ai)
    logger.info(
        "Starting Lingo WhatsApp bot server (mode={}, phone_number_id={})",
        settings.bot_mode,
        settings.whatsapp_phone_number_id,
    )

    async with aiohttp.ClientSession() as session:
        whatsapp_client = WhatsAppClient(
            whatsapp_token=settings.whatsapp_token,
            phone_number_id=settings.whatsapp_phone_number_id,
            session=session,
            whatsapp_secret=settings.whatsapp_app_secret,
        )
        try:
            yield
        finally:
            logger.info("Terminating active WhatsApp calls...")
            await whatsapp_client.terminate_all_calls()
            whatsapp_client = None
            logger.info("Shutdown complete")


app = FastAPI(
    title="Lingo WhatsApp Voice Bot",
    description="WhatsApp Cloud API voice bot with ElevenLabs STT/TTS and OpenAI LLM, or echo mode for testing.",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/whatsapp")
async def verify_webhook(request: Request):
    """Meta webhook verification challenge."""
    if whatsapp_client is None or settings is None:
        raise HTTPException(status_code=503, detail="Service unavailable")

    params = dict(request.query_params)
    try:
        challenge = await whatsapp_client.handle_verify_webhook_request(
            params=params,
            expected_verification_token=settings.whatsapp_webhook_verification_token,
        )
        logger.info("WhatsApp webhook verification succeeded")
        # Meta expects the raw challenge as plain text (numeric string).
        return PlainTextResponse(content=str(challenge))
    except ValueError as exc:
        logger.warning("WhatsApp webhook verification failed: {}", exc)
        raise HTTPException(status_code=403, detail="Verification failed") from exc


@app.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
):
    """Handle inbound WhatsApp call connect / terminate webhooks."""
    if whatsapp_client is None or settings is None:
        raise HTTPException(status_code=503, detail="Service unavailable")

    raw_body = await request.body()
    try:
        body = WhatsAppWebhookRequest.model_validate_json(raw_body)
    except Exception as exc:
        logger.warning("Invalid WhatsApp webhook payload: {}", exc)
        raise HTTPException(status_code=400, detail="Invalid request body") from exc

    if body.object != "whatsapp_business_account":
        raise HTTPException(status_code=400, detail="Invalid object type")

    async def connection_callback(
        connection: SmallWebRTCConnection,
        call: WhatsAppConnectCall,
    ) -> None:
        logger.info("Accepted WhatsApp call id={} from={}", call.id, call.from_)
        background_tasks.add_task(run_bot, connection, settings, call)

    try:
        result = await whatsapp_client.handle_webhook_request(
            body,
            connection_callback,
            raw_body=raw_body,
            sha256_signature=x_hub_signature_256,
        )
        return {"status": "success", "handled": result}
    except ValueError as exc:
        logger.warning("Rejected WhatsApp webhook: {}", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to process WhatsApp webhook")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lingo WhatsApp echo bot server")
    parser.add_argument("--host", default=None, help="Bind host (default: HOST env or 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: PORT env or 7860)")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = create_parser().parse_args(argv)
    cfg = Settings.from_env(require_whatsapp=True)

    logger.remove()
    logger.add(sys.stderr, level="TRACE" if args.verbose else "INFO")

    host = args.host or cfg.host
    port = args.port or cfg.port
    logger.info("Listening on http://{}:{} (webhook path /whatsapp)", host, port)
    uvicorn.run(
        "lingo.server:app",
        host=host,
        port=port,
        log_level="info",
        factory=False,
    )


if __name__ == "__main__":
    main()
