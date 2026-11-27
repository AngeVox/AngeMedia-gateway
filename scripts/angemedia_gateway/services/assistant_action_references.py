"""动作引用的有界内容观察；远程引用只核对URL，不声称已经验证远端内容。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .. import config as C
from ..assistant_action_contract import ActionError
from ..reference_images import MAX_IMAGE_REFERENCE_COUNT, REFERENCE_IMAGE_MAX_BYTES, validate_gateway_image_reference
from ..security import validate_provider_reference_url


def _stat(path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def _local_path(locator):
    validate_gateway_image_reference(locator)
    prefix, base = ('/uploads/', C.UPLOAD_DIR) if locator.startswith('/uploads/') else ('/generated/', C.OUTPUT_DIR)
    path = (base / locator[len(prefix):]).resolve(strict=True)
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ActionError('action_reference_unavailable')
    return path


def inspect_references(request):
    locators = []
    for field in ('image', 'images', 'reference_images', 'mask', 'markup', 'first_frame', 'last_frame', 'audios', 'videos'):
        value = request.get(field)
        values = value if isinstance(value, list) else [value] if value else []
        for item in values:
            locator = item.get('url') if isinstance(item, dict) else item
            if locator and locator not in locators:
                locators.append(locator)
    # 蒙版与标注互斥，且都不占参考图槽；型号数量由媒体准备服务校验。
    if len(locators) > MAX_IMAGE_REFERENCE_COUNT + 1:
        raise ActionError('action_reference_limit')
    references, checks = [], []
    try:
        for locator in locators:
            if locator.startswith(('/uploads/', '/generated/')):
                path = _local_path(locator); before = _stat(path)
                if not 0 < before[2] <= REFERENCE_IMAGE_MAX_BYTES:
                    raise ActionError('action_reference_limit')
                digest = hashlib.sha256(); total = 0
                with path.open('rb') as stream:
                    while chunk := stream.read(65536):
                        total += len(chunk)
                        if total > REFERENCE_IMAGE_MAX_BYTES:
                            raise ActionError('action_reference_limit')
                        digest.update(chunk)
                if _stat(path) != before or total != before[2]:
                    raise ActionError('action_environment_changed')
                references.append({'locator': locator, 'kind': 'gateway_image', 'sha256': digest.hexdigest(), 'bytes': total})
                checks.append({'locator': locator, 'stat': before})
            else:
                references.append({'locator': validate_provider_reference_url(locator), 'kind': 'remote_url', 'content_verified': False})
    except (OSError, ValueError, TypeError):
        raise ActionError('action_reference_unavailable') from None
    return references, checks


def references_unchanged(checks):
    try:
        return all(_stat(_local_path(item['locator'])) == item['stat'] for item in checks)
    except (OSError, ValueError, ActionError):
        return False
