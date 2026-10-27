"""Bounded user reference images, separate from unmodified device observations."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_VISUAL_REQUEST_BYTES = 6 * 1024 * 1024
MAX_STROKES = 64
MAX_POINTS = 2000


def validate_visual(value):
    if not isinstance(value, dict) or set(value) not in (
        {'takeover_id', 'source_sha256', 'strokes'}, {'upload_data_url', 'strokes'}
    ):
        raise ValueError('截图输入必须是本次接管截图或上传的图片，不能指定文件路径')
    strokes = value['strokes']
    if not isinstance(strokes, list) or len(strokes) > MAX_STROKES:
        raise ValueError('单张截图最多保留 64 个标注')
    count = 0
    for stroke in strokes:
        if not isinstance(stroke, dict) or set(stroke) != {'tool', 'points'}:
            raise ValueError('标注格式无效')
        tool, points = stroke['tool'], stroke['points']
        if (tool not in ('circle', 'cross', 'pen') or not isinstance(points, list)
                or not 2 <= len(points) <= (512 if tool == 'pen' else 2)):
            raise ValueError('标注必须是圆圈、叉号或画笔轨迹')
        count += len(points)
        for point in points:
            if (not isinstance(point, list) or len(point) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                           for v in point)):
                raise ValueError('标注坐标必须在图片的 0..1 范围内')
    if count > MAX_POINTS:
        raise ValueError('画笔轨迹过长，请撤销部分标注后重试')
    if 'upload_data_url' in value:
        url = value['upload_data_url']
        if not isinstance(url, str) or len(url) > (MAX_UPLOAD_BYTES + 2) // 3 * 4 + 64:
            raise ValueError('上传截图不能超过 4 MB')
    elif not isinstance(value['takeover_id'], str) or not isinstance(value['source_sha256'], str):
        raise ValueError('缺少接管截图标识')
    # Bind retry identity to the exact image and annotations without persisting base64.
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _decode(data):
    try:
        with Image.open(io.BytesIO(data)) as original:
            if (original.format not in {'PNG', 'JPEG', 'WEBP'} or getattr(original, 'n_frames', 1) != 1
                    or min(original.size) < 16 or max(original.size) > 4096
                    or original.width * original.height > 12_000_000):
                raise ValueError('仅支持静态 PNG/JPEG/WebP 截图，边长 16..4096，最多 1200 万像素')
            # Normalize EXIF orientation and strip metadata; no crop or resize.
            oriented = ImageOps.exif_transpose(original).convert('RGBA')
            result = Image.new('RGBA', oriented.size, 'white')
            result.alpha_composite(oriented)
            return result.convert('RGB')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError('无法读取截图，请选择完整的 PNG/JPEG/WebP 图片') from error


def _write(image, path):
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    data = buffer.getvalue()
    if len(data) > 10_000_000:
        raise ValueError('处理后的截图超过 10 MB，请使用较小的图片')
    # Request IDs cannot overwrite accepted evidence.
    with path.open('xb') as output:
        output.write(data)
    return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
            'width': image.width, 'height': image.height}


def save_visual(value, *, segment, directory, request_id):
    """Called after phase/id checks; never capture a device on an HTTP thread."""
    if directory is None:
        raise ValueError('本次运行没有截图归档目录')
    directory = Path(directory) / 'interventions'
    source = None
    if 'upload_data_url' in value:
        prefix, separator, encoded = value['upload_data_url'].partition(',')
        if not separator or prefix not in {'data:image/png;base64', 'data:image/jpeg;base64',
                                           'data:image/webp;base64'}:
            raise ValueError('请选择 PNG/JPEG/WebP 截图')
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise ValueError('截图编码无效') from error
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError('上传截图不能超过 4 MB')
        origin = 'user_upload'
    else:
        source = segment.get('before')
        if (value['takeover_id'] != segment['id'] or not source
                or value['source_sha256'] != source['sha256']):
            raise ValueError('截图与当前接管不匹配，请重新选择截图')
        path = Path(source['path'])
        if path.resolve() != (directory / f'{segment["id"]}-before.png').resolve():
            raise ValueError('截图不属于本次运行')
        if not path.is_file() or path.stat().st_size > 10_000_000:
            raise ValueError('接管截图缺失或过大')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != source['sha256']:
            raise ValueError('接管截图已变化，请重新选择截图')
        origin = 'takeover_snapshot'
    image = _decode(data)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        if source is None:
            path = directory / f'visual-{request_id}-source.png'
            source = _write(image, path)
            written.append(path)
        draw = ImageDraw.Draw(image)
        width = max(2, round(min(image.size) * .006))
        for stroke in value['strokes']:
            points = [(round(p[0] * (image.width - 1)), round(p[1] * (image.height - 1)))
                      for p in stroke['points']]
            (x1, y1), (x2, y2) = points[0], points[-1]
            box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            for color, thickness in [('white', width + 2), ('#e5243b', width)]:
                if stroke['tool'] == 'circle':
                    draw.ellipse(box, outline=color, width=thickness)
                elif stroke['tool'] == 'cross':
                    draw.line([(box[0], box[1]), (box[2], box[3])], fill=color, width=thickness)
                    draw.line([(box[0], box[3]), (box[2], box[1])], fill=color, width=thickness)
                else:
                    draw.line(points, fill=color, width=thickness, joint='curve')
        path = directory / f'visual-{request_id}-annotated.png'
        annotated = _write(image, path)
        written.append(path)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return {'origin': origin, 'source': dict(source), 'image': annotated,
            'strokes': value['strokes'], 'coordinate_space': 'normalized_reference_image',
            'meaning': 'User-drawn guidance, not a current observation or executed action.'}
