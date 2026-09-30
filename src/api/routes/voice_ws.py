from fastapi import APIRouter, WebSocket
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams

from src.api.routes.ws_auth import authenticate_websocket
from src.channels.voice.pipeline import build_voice_pipeline

router = APIRouter()


@router.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket):
    container = websocket.app.state.container
    principal = await authenticate_websocket(websocket, container.settings)
    await websocket.accept()

    ws_transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=ProtobufFrameSerializer(),
        ),
    )

    pipeline, worker, runner = await build_voice_pipeline(
        settings=container.settings,
        agent=container.agent,
        manager=container.manager,
        principal=principal,
        session_id=f"web-{principal['user_id']}",
        transport=ws_transport,
    )

    await runner.run()