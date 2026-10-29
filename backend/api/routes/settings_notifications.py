"""LAN management routes protected by the existing web session/same-origin policy."""
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse, Response

from backend.api.routes.notification_models import NotificationPatch
from backend.api.security import require_same_origin_mutation
from backend.application.usecases.notification_settings import get_settings, update_settings, send_test
from backend.application.usecases import feishu_connect

class SecretSafeRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handle(request):
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse({'detail': 'invalid_notification_settings'}, status_code=422)
        return handle


router = APIRouter(prefix='/api/settings/notifications', tags=['notifications'], route_class=SecretSafeRoute)


@router.get('')
def get_notifications(request: Request):
    return get_settings(request.app.state.base_dir)


@router.put('', dependencies=[Depends(require_same_origin_mutation)])
def put_notifications(request: Request, payload: NotificationPatch):
    return update_settings(request.app.state.base_dir, payload.model_dump(exclude_none=True))


@router.post('/feishu/connect', dependencies=[Depends(require_same_origin_mutation)])
def connect_feishu(request: Request):
    return feishu_connect.start_connection(request.app.state.base_dir)


@router.get('/feishu/connect')
def feishu_connection():
    return feishu_connect.get_connection()


@router.get('/feishu/connect/qr.png')
def feishu_connection_qr():
    try:
        image = feishu_connect.connection_qr()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='qr_unavailable') from None
    return Response(image, media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.delete('/feishu/connect', dependencies=[Depends(require_same_origin_mutation)])
def cancel_feishu_connection():
    return feishu_connect.cancel_connection()


@router.post('/test', dependencies=[Depends(require_same_origin_mutation)])
def test_notifications(request: Request):
    return send_test(request.app.state.base_dir)
