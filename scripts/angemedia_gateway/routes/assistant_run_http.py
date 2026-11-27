"""新旧助手入口共用运行服务查找和异常响应。"""
from fastapi import HTTPException, Request

from ..assistant_run_access import RunAuthenticationExpired, RunCursorInvalid, RunUnavailable
from ..assistant_runtime_contract import RunBusy, RunNotFound, RunRequestConflict
from ..repositories.assistant_conversations import ConversationNotFound
from ..services.assistant_run_service import AssistantRunService


def require_run_service(request: Request) -> AssistantRunService:
    service = getattr(request.app.state, "assistant_run_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail={"code": "run_service_not_started"})
    return service


async def run_result(operation):
    try:
        return await operation
    except RunAuthenticationExpired:
        raise HTTPException(status_code=401, detail={"code": "authentication_expired"}) from None
    except (RunNotFound, ConversationNotFound):
        raise HTTPException(status_code=404, detail={"code": "run_not_found"}) from None
    except RunRequestConflict:
        raise HTTPException(status_code=409, detail={"code": "request_conflict"}) from None
    except RunCursorInvalid:
        raise HTTPException(status_code=409, detail={"code": "event_cursor_ahead"}) from None
    except RunBusy:
        raise HTTPException(status_code=429, detail={"code": "run_queue_full"}) from None
    except RunUnavailable:
        raise HTTPException(status_code=503, detail={"code": "run_execution_unavailable"}) from None
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "invalid_run_request"}) from None
