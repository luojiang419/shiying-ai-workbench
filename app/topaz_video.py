"""复用 SHIYIN-AI canvas_core/topaz_video.py 的经典 Topaz 适配。"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TOPAZ_VENDOR = "Topaz Labs LLC"
TOPAZ_PRODUCT_DIR = Path("Topaz Labs LLC") / "Topaz Video AI"
TOPAZ_CURRENT_PRODUCT_DIR = Path("Topaz Labs LLC") / "Topaz Video"
TOPAZ_UPSCALE_FILTER = "tvai_up"
TOPAZ_MODEL_PREFIXES = frozenset(
    {
        "aaa",
        "ahq",
        "alq",
        "alqs",
        "amq",
        "amqs",
        "ddv",
        "dtd",
        "dtds",
        "dtv",
        "dtvs",
        "gcg",
        "ghq",
        "iris",
        "nyx",
        "prob",
        "rhea",
        "thd",
        "thf",
        "thm",
    }
)
TOPAZ_ENCODERS = frozenset({"auto", "h264_nvenc", "hevc_nvenc"})
TOPAZ_AUDIO_MODES = frozenset({"aac", "copy", "none"})
TOPAZ_QUALITY_QP = {"high": 18, "balanced": 23, "compact": 28}
TOPAZ_TARGETS = frozenset({"2x", "4x", "1080p", "1440p", "2160p"})
TOPAZ_TARGET_SHORT_EDGE = {"1080p": 1080, "1440p": 1440, "2160p": 2160}
_MODEL_LABELS = {
    "prob": ("Proteus", "通用精细增强"),
    "iris": ("Iris", "人脸与低清素材"),
    "rhea": ("Rhea", "高质量通用增强"),
    "ahq": ("Artemis HQ", "高质量素材"),
    "amq": ("Artemis MQ", "中等质量素材"),
    "alq": ("Artemis LQ", "低质量素材"),
    "aaa": ("Artemis AA", "锯齿与摩尔纹"),
    "gcg": ("Gaia CG", "动画与计算机图形"),
    "ghq": ("Gaia HQ", "高质量实拍"),
    "thf": ("Theia Fine Tune", "细节精调"),
    "thd": ("Theia Detail", "细节强化"),
    "thm": ("Theia Motion", "运动画面"),
    "dtv": ("Dione TV", "隔行电视素材"),
    "ddv": ("Dione DV", "隔行 DV 素材"),
    "dtd": ("Dione Robust", "隔行稳健处理"),
    "nyx": ("Nyx", "降噪"),
}


class TopazVideoError(RuntimeError):
    pass


@dataclass(frozen=True)
class TopazSignature:
    status: str = "Unknown"
    signer: str = ""
    version: str = ""

    @property
    def valid(self) -> bool:
        return self.status.lower() == "valid" and TOPAZ_VENDOR.lower() in self.signer.lower()


@dataclass(frozen=True)
class TopazInstallation:
    install_dir: Path | None
    ffmpeg_path: Path | None
    ffprobe_path: Path | None
    model_dir: Path | None
    model_data_dir: Path | None
    signature: TopazSignature
    filter_available: bool = False
    error: str = ""

    @property
    def ready(self) -> bool:
        return bool(
            self.install_dir
            and self.ffmpeg_path
            and self.ffprobe_path
            and self.model_dir
            and self.model_data_dir
            and self.signature.valid
            and self.filter_available
            and not self.error
        )

    def public(self) -> dict[str, Any]:
        return {
            "installed": bool(self.install_dir and self.ffmpeg_path and self.ffprobe_path),
            "ready": self.ready,
            "install_dir": str(self.install_dir or ""),
            "ffmpeg_path": str(self.ffmpeg_path or ""),
            "ffprobe_path": str(self.ffprobe_path or ""),
            "model_dir": str(self.model_dir or ""),
            "model_data_dir": str(self.model_data_dir or ""),
            "version": self.signature.version,
            "signature_status": self.signature.status,
            "signature_valid": self.signature.valid,
            "filter_available": self.filter_available,
            "error": self.error,
        }


@dataclass(frozen=True)
class TopazUpscaleSettings:
    model: str = "prob-4"
    target: str = "2x"
    quality: str = "balanced"
    preblur: float = 0.0
    noise: float = 0.0
    details: float = 0.0
    halo: float = 0.0
    blur: float = 0.0
    compression: float = 0.0
    pre_noise: float = 0.0
    estimate: int = 0
    blend: float = 0.0
    grain: float = 0.0
    grain_size: float = 0.0
    device: str = "-2"
    vram: float = 1.0
    instances: int = 0
    download_models: bool = True
    color_correction: bool = True
    encoder: str = "auto"
    audio_mode: str = "aac"
    audio_bitrate_kbps: int = 320
    output_width: int = 0
    output_height: int = 0

    def validated(self, *, available_models: Iterable[str] | None = None) -> TopazUpscaleSettings:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", self.model):
            raise TopazVideoError("Topaz 模型标识格式不正确")
        model_set = {str(item) for item in (available_models or []) if str(item)}
        if model_set and self.model not in model_set:
            raise TopazVideoError(f"当前 Topaz 安装中不存在模型：{self.model}")
        if self.target not in TOPAZ_TARGETS:
            raise TopazVideoError("Topaz 输出尺寸必须是 2x、4x、1080p、1440p 或 2160p")
        if self.quality not in TOPAZ_QUALITY_QP:
            raise TopazVideoError("Topaz 输出质量必须是 high、balanced 或 compact")
        for label, value in (
            ("反锯齿/去模糊", self.preblur),
            ("降噪", self.noise),
            ("细节恢复", self.details),
            ("去光晕", self.halo),
            ("锐化", self.blur),
            ("压缩修复", self.compression),
        ):
            if not -1.0 <= float(value) <= 1.0:
                raise TopazVideoError(f"{label}必须位于 -1 到 1")
        if not 0 <= int(self.estimate) <= 100:
            raise TopazVideoError("自动分析帧数必须位于 0 到 100")
        if not 0.0 <= float(self.pre_noise) <= 0.1:
            raise TopazVideoError("预加噪必须位于 0 到 0.1")
        if not 0.0 <= float(self.blend) <= 1.0:
            raise TopazVideoError("原始细节混合必须位于 0 到 1")
        if not 0.0 <= float(self.grain) <= 0.1:
            raise TopazVideoError("胶片颗粒必须位于 0 到 0.1")
        if not 0.0 <= float(self.grain_size) <= 5.0:
            raise TopazVideoError("颗粒尺寸必须位于 0 到 5")
        if not re.fullmatch(r"(?:-2|-1|\d+(?:\.\d+)*)", str(self.device)):
            raise TopazVideoError("Topaz 计算设备格式不正确")
        if not 0.1 <= float(self.vram) <= 1.0:
            raise TopazVideoError("显存占用比例必须位于 0.1 到 1")
        if not 0 <= int(self.instances) <= 3:
            raise TopazVideoError("并行模型实例必须位于 0 到 3")
        if self.encoder not in TOPAZ_ENCODERS:
            raise TopazVideoError("不支持的 Topaz 输出编码器")
        if self.audio_mode not in TOPAZ_AUDIO_MODES:
            raise TopazVideoError("不支持的 Topaz 音频模式")
        if not 64 <= int(self.audio_bitrate_kbps) <= 512:
            raise TopazVideoError("音频码率必须位于 64 到 512 kbps")
        if self.output_width and (self.output_width < 16 or self.output_width > 16384 or self.output_width % 2):
            raise TopazVideoError("输出宽度必须是 16-16384 之间的偶数")
        if self.output_height and (self.output_height < 16 or self.output_height > 16384 or self.output_height % 2):
            raise TopazVideoError("输出高度必须是 16-16384 之间的偶数")
        return self

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _unique_paths(values: Iterable[Path]) -> list[Path]:
    unique: dict[str, Path] = {}
    for value in values:
        text = str(value).strip()
        if text:
            unique.setdefault(os.path.normcase(os.path.abspath(text)), Path(text))
    return list(unique.values())


def candidate_topaz_install_dirs(configured_dir: str = "") -> list[Path]:
    candidates: list[Path] = []
    configured = str(configured_dir or os.environ.get("TOPAZ_VIDEO_AI_DIR") or "").strip().strip('"')
    if configured:
        candidate = Path(configured).expanduser()
        candidates.append(candidate.parent if candidate.name.lower() in {"ffmpeg.exe", "topaz video ai.exe", "topaz video.exe"} else candidate)
    for variable in ("ProgramW6432", "ProgramFiles"):
        value = str(os.environ.get(variable) or "").strip()
        if value:
            candidates.extend((Path(value) / TOPAZ_CURRENT_PRODUCT_DIR, Path(value) / TOPAZ_PRODUCT_DIR))
    if os.name == "nt":
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:\\")
            if drive.exists():
                candidates.extend((drive / "Program Files" / TOPAZ_CURRENT_PRODUCT_DIR, drive / "Program Files" / TOPAZ_PRODUCT_DIR))
    return _unique_paths(candidates)


def registry_topaz_model_data_dir() -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    for product in ("Topaz Video", "Topaz Video AI"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Topaz Labs LLC\{product}") as key:
                value = str(winreg.QueryValueEx(key, "veaiDataFolder")[0]).strip()
        except OSError:
            continue
        if value:
            return Path(value).expanduser()
    return None


def candidate_topaz_model_dirs() -> list[Path]:
    candidates: list[Path] = []
    configured = str(os.environ.get("TVAI_MODEL_DIR") or "").strip().strip('"')
    if configured:
        candidates.append(Path(configured).expanduser())
    program_data = str(os.environ.get("PROGRAMDATA") or "").strip()
    if program_data:
        candidates.extend((Path(program_data) / TOPAZ_CURRENT_PRODUCT_DIR / "models", Path(program_data) / TOPAZ_PRODUCT_DIR / "models"))
    if os.name == "nt":
        candidates.extend((Path("C:/ProgramData") / TOPAZ_CURRENT_PRODUCT_DIR / "models", Path("C:/ProgramData") / TOPAZ_PRODUCT_DIR / "models"))
    return _unique_paths(candidates)


def candidate_topaz_model_data_dirs() -> list[Path]:
    candidates: list[Path] = []
    configured = str(os.environ.get("TVAI_MODEL_DATA_DIR") or "").strip().strip('"')
    if configured:
        candidates.append(Path(configured).expanduser())
    registry = registry_topaz_model_data_dir()
    if registry:
        candidates.append(registry)
    candidates.extend(candidate_topaz_model_dirs())
    return _unique_paths(candidates)


def inspect_authenticode(path: Path) -> TopazSignature:
    if os.name != "nt":
        return TopazSignature(status="Unsupported")
    powershell = shutil.which("pwsh.exe") or shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return TopazSignature(status="Unavailable")
    target_variable = "MINIMAX_TOPAZ_SIGNATURE_TARGET"
    script = (
        f"$p=[Environment]::GetEnvironmentVariable('{target_variable}','Process');"
        "$s=Get-AuthenticodeSignature -LiteralPath $p;"
        "$v=(Get-Item -LiteralPath $p).VersionInfo.ProductVersion;"
        "if(!$v){foreach($n in @('Topaz Video.exe','Topaz Video AI.exe')){"
        "$e=Join-Path (Split-Path -Parent $p) $n;if(Test-Path -LiteralPath $e){"
        "$v=(Get-Item -LiteralPath $e).VersionInfo.ProductVersion;if($v){break}}}};"
        "[pscustomobject]@{Status=[string]$s.Status;Signer=[string]$s.SignerCertificate.Subject;Version=[string]$v}"
        "|ConvertTo-Json -Compress"
    )
    child_environment = dict(os.environ)
    child_environment[target_variable] = str(path)
    try:
        result = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            env=child_environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        payload = json.loads(str(result.stdout or "").strip()) if result.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        payload = {}
    return TopazSignature(
        status=str(payload.get("Status") or "Unknown"),
        signer=str(payload.get("Signer") or ""),
        version=str(payload.get("Version") or ""),
    )


def topaz_filter_available(ffmpeg_path: Path, runner: Callable[..., subprocess.CompletedProcess[Any]] | None = None) -> bool:
    invoke = runner or subprocess.run
    try:
        result = invoke(
            [str(ffmpeg_path), "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(re.search(r"\btvai_up\b", f"{result.stdout}\n{result.stderr}"))


def resolve_topaz_installation(
    configured_dir: str = "",
    *,
    signature_inspector: Callable[[Path], TopazSignature] = inspect_authenticode,
    filter_probe: Callable[[Path], bool] = topaz_filter_available,
) -> TopazInstallation:
    candidates = candidate_topaz_install_dirs(configured_dir)
    # 用户明确选定路径时应校验该路径，不能静默改用另一份安装。
    if str(configured_dir).strip():
        candidates = candidates[:1]
    install_dir = next(
        (
            root.resolve()
            for root in candidates
            if (root / "ffmpeg.exe").is_file() and (root / "ffprobe.exe").is_file()
        ),
        None,
    )
    if not install_dir:
        return TopazInstallation(None, None, None, None, None, TopazSignature(), error="未检测到 Topaz Video AI")
    ffmpeg_path = install_dir / "ffmpeg.exe"
    ffprobe_path = install_dir / "ffprobe.exe"
    model_dir = next((path.resolve() for path in [install_dir / "models", *candidate_topaz_model_dirs()] if (path / "tvai.tz").is_file()), None)
    model_data_dir = next((path.resolve() for path in candidate_topaz_model_data_dirs() if path.is_dir()), None)
    signature = signature_inspector(ffmpeg_path)
    available = filter_probe(ffmpeg_path) if signature.valid else False
    error = ""
    if not signature.valid:
        error = "Topaz FFmpeg 数字签名无效，已阻止运行"
    elif not model_dir:
        error = "未找到 Topaz 模型定义目录"
    elif not model_data_dir:
        error = "未找到 Topaz 模型数据目录"
    elif not available:
        error = "当前 Topaz FFmpeg 不包含 tvai_up 滤镜"
    return TopazInstallation(
        install_dir=install_dir,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        model_dir=model_dir,
        model_data_dir=model_data_dir,
        signature=signature,
        filter_available=available,
        error=error,
    )


def available_topaz_models(model_dir: Path | None) -> list[dict[str, str]]:
    if not model_dir or not model_dir.is_dir():
        return []
    models: list[dict[str, str]] = []
    for path in model_dir.glob("*.json"):
        model_id = path.stem.lower()
        prefix = model_id.split("-", 1)[0]
        if prefix not in TOPAZ_MODEL_PREFIXES:
            continue
        try:
            definition = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(definition, dict) or definition.get("isNeuroserverModel") or definition.get("enabled") == 0:
            continue
        family, description = _MODEL_LABELS.get(prefix, (prefix.upper(), "视频增强"))
        models.append(
            {
                "id": model_id,
                "name": f"{family} {model_id.rsplit('-', 1)[-1]}",
                "family": family,
                "description": description,
            }
        )
    def version_key(item: dict[str, str]) -> tuple[str, int]:
        match = re.search(r"-(\d+)$", item["id"])
        return item["family"], -(int(match.group(1)) if match else 0)

    return sorted(models, key=version_key)


def preferred_topaz_model(models: Iterable[dict[str, str]]) -> str:
    ids = [str(item.get("id") or "") for item in models]
    for candidate in ("prob-4", "prob-3", "prob-2", "amq-13", "ahq-12"):
        if candidate in ids:
            return candidate
    return ids[0] if ids else "prob-4"


def topaz_child_environment(installation: TopazInstallation) -> dict[str, str]:
    if not installation.model_dir or not installation.model_data_dir:
        raise TopazVideoError("Topaz 模型目录尚未就绪")
    environment = dict(os.environ)
    environment["TVAI_MODEL_DIR"] = str(installation.model_dir)
    environment["TVAI_MODEL_DATA_DIR"] = str(installation.model_data_dir)
    return environment


def _even(value: float) -> int:
    return max(16, int(math.floor(float(value) / 2.0) * 2))


def resolve_target_dimensions(width: int, height: int, target: str) -> tuple[int, int]:
    width = int(width)
    height = int(height)
    if width < 1 or height < 1:
        raise TopazVideoError("无法读取输入视频尺寸")
    if target == "2x":
        return _even(width * 2), _even(height * 2)
    if target == "4x":
        return _even(width * 4), _even(height * 4)
    short_edge = TOPAZ_TARGET_SHORT_EDGE.get(target)
    if not short_edge:
        raise TopazVideoError("不支持的 Topaz 输出尺寸")
    if width >= height:
        out_height = short_edge
        out_width = _even(width * short_edge / height)
    else:
        out_width = short_edge
        out_height = _even(height * short_edge / width)
    if out_width > 16384 or out_height > 16384:
        raise TopazVideoError("计算后的 Topaz 输出尺寸超过 16384 像素")
    return _even(out_width), _even(out_height)


def topaz_filter_expression(settings: TopazUpscaleSettings) -> str:
    settings.validated()
    values: list[tuple[str, str]] = [("model", settings.model)]
    if settings.output_width and settings.output_height:
        values.extend((("scale", "0"), ("w", str(settings.output_width)), ("h", str(settings.output_height))))
    else:
        values.append(("scale", settings.target.removesuffix("x") if settings.target in {"2x", "4x"} else "0"))
    values.extend(
        (
            ("preblur", f"{settings.preblur:g}"),
            ("noise", f"{settings.noise:g}"),
            ("details", f"{settings.details:g}"),
            ("halo", f"{settings.halo:g}"),
            ("blur", f"{settings.blur:g}"),
            ("compression", f"{settings.compression:g}"),
            ("prenoise", f"{settings.pre_noise:g}"),
            ("estimate", str(settings.estimate)),
            ("blend", f"{settings.blend:g}"),
            ("grain", f"{settings.grain:g}"),
            ("gsize", f"{settings.grain_size:g}"),
            ("device", settings.device),
            ("vram", f"{settings.vram:g}"),
            ("instances", str(settings.instances)),
            ("download", "1" if settings.download_models else "0"),
            ("kcolor", "1" if settings.color_correction else "0"),
        )
    )
    return TOPAZ_UPSCALE_FILTER + "=" + ":".join(f"{key}={value}" for key, value in values)


def resolve_topaz_output_encoder(settings: TopazUpscaleSettings) -> str:
    """Resolve the requested encoder after the final output dimensions are known."""
    settings.validated()
    largest_edge = max(int(settings.output_width or 0), int(settings.output_height or 0))
    if settings.encoder == "auto":
        # NVENC H.264 on several consumer GPU generations rejects dimensions above 4096,
        # while HEVC NVENC supports the 8K output produced by a 4x upscale of 1080p.
        return "hevc_nvenc" if largest_edge > 4096 else "h264_nvenc"
    if settings.encoder == "h264_nvenc" and largest_edge > 4096:
        raise TopazVideoError(
            "当前输出尺寸超过 H.264 NVIDIA 编码器的 4096 像素限制；请在高级设置中选择“自动（推荐）”或 H.265 NVIDIA"
        )
    return settings.encoder


def humanize_topaz_ffmpeg_error(
    lines: Iterable[str],
    *,
    encoder: str = "",
    output_width: int = 0,
    output_height: int = 0,
) -> str:
    text_lines = [str(line).strip() for line in lines if str(line).strip()]
    combined = "\n".join(text_lines)
    if re.search(r"Width\s+\d+\s+exceeds\s+4096", combined, re.IGNORECASE):
        return (
            f"输出尺寸 {int(output_width)}×{int(output_height)} 超过 H.264 NVIDIA 编码器的 4096 像素限制。"
            "请改用“自动（推荐）”或 H.265 NVIDIA 后重试。"
        )
    if re.search(r"No capable devices found|Error while opening encoder", combined, re.IGNORECASE):
        codec = "H.265 NVIDIA" if encoder == "hevc_nvenc" else "H.264 NVIDIA"
        return f"{codec} 编码器无法在当前显卡上启动。请在高级设置中改用“自动（推荐）”后重试，并确认显卡驱动可用。"
    useful = [
        line
        for line in text_lines
        if "No accelerated colorspace conversion found" not in line
    ]
    detail = "\n".join((useful or text_lines)[-12:]).strip()
    return (detail or "Topaz FFmpeg 处理失败")[-2000:]


def build_topaz_upscale_command(
    installation: TopazInstallation,
    input_path: Path,
    output_path: Path,
    settings: TopazUpscaleSettings,
    *,
    available_models: Iterable[str] | None = None,
) -> list[str]:
    if not installation.ready or not installation.ffmpeg_path:
        raise TopazVideoError(installation.error or "Topaz Video AI 尚未就绪")
    source = input_path.expanduser().resolve(strict=True)
    destination = output_path.expanduser().resolve(strict=False)
    if not source.is_file():
        raise TopazVideoError("Topaz 输入视频不存在")
    if source == destination:
        raise TopazVideoError("Topaz 输出路径不能覆盖输入视频")
    if destination.suffix.lower() not in {".mp4", ".mov", ".mkv"}:
        raise TopazVideoError("Topaz 输出只支持 MP4、MOV 或 MKV")
    if not destination.parent.is_dir():
        raise TopazVideoError("Topaz 输出目录不存在")
    settings.validated(available_models=available_models)
    command = [
        str(installation.ffmpeg_path),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        topaz_filter_expression(settings),
    ]
    qp = str(TOPAZ_QUALITY_QP[settings.quality])
    encoder = resolve_topaz_output_encoder(settings)
    if encoder == "h264_nvenc":
        command.extend(
            [
                "-c:v",
                "h264_nvenc",
                "-profile:v",
                "high",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "p7",
                "-tune",
                "hq",
                "-rc",
                "constqp",
                "-qp",
                qp,
                "-spatial_aq",
                "1",
                "-aq-strength",
                "15",
                "-b:v",
                "0",
            ]
        )
    elif encoder == "hevc_nvenc":
        command.extend(
            [
                "-c:v",
                "hevc_nvenc",
                "-profile:v",
                "main",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "p7",
                "-tune",
                "hq",
                "-rc",
                "constqp",
                "-qp",
                qp,
                "-b:v",
                "0",
                "-tag:v",
                "hvc1",
            ]
        )
    if settings.audio_mode == "none":
        command.append("-an")
    elif settings.audio_mode == "copy":
        command.extend(["-c:a", "copy"])
    else:
        command.extend(["-c:a", "aac", "-b:a", f"{settings.audio_bitrate_kbps}k"])
    command.extend(
        [
            "-map_metadata",
            "0",
            "-movflags",
            "frag_keyframe+empty_moov+delay_moov+use_metadata_tags+write_colr",
            "-progress",
            "pipe:1",
            "-nostats",
            str(destination),
        ]
    )
    return command


def _finite_float(value: Any, default: float | None = 0.0) -> float | None:
    text = str(value if value is not None else "").strip()
    if not text or text.lower() in {"n/a", "nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity"}:
        return default
    try:
        number = float(text)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _finite_int(value: Any, default: int | None = 0) -> int | None:
    number = _finite_float(value, None)
    if number is None:
        return default
    try:
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return default


def parse_ffprobe_video(payload: str) -> dict[str, Any]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TopazVideoError("Topaz ffprobe 返回了无效数据") from exc
    streams = data.get("streams") if isinstance(data, dict) else []
    stream = next((item for item in streams or [] if str(item.get("codec_type") or "") == "video"), None)
    if not isinstance(stream, dict):
        raise TopazVideoError("输入文件不包含视频流")
    format_data = data.get("format") if isinstance(data.get("format"), dict) else {}
    duration = _finite_float(stream.get("duration"), None)
    if duration is None:
        duration = _finite_float(format_data.get("duration"), 0.0)
    width = int(_finite_int(stream.get("width"), 0) or 0)
    height = int(_finite_int(stream.get("height"), 0) or 0)
    if width <= 0 or height <= 0:
        raise TopazVideoError("无法读取输入视频的有效分辨率")
    return {
        "width": width,
        "height": height,
        "duration": max(0.0, float(duration or 0.0)),
        "codec": str(stream.get("codec_name") or ""),
        "pix_fmt": str(stream.get("pix_fmt") or ""),
    }


def probe_video(installation: TopazInstallation, input_path: Path) -> dict[str, Any]:
    if not installation.ready or not installation.ffprobe_path:
        raise TopazVideoError(installation.error or "Topaz Video AI 尚未就绪")
    source = input_path.expanduser().resolve(strict=True)
    try:
        result = subprocess.run(
            [
                str(installation.ffprobe_path),
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,pix_fmt,duration:format=duration",
                "-of",
                "json",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TopazVideoError(f"无法读取输入视频信息：{exc}") from exc
    if result.returncode != 0:
        raise TopazVideoError((result.stderr or "无法读取输入视频信息").strip()[:500])
    return parse_ffprobe_video(result.stdout)


def parse_ffmpeg_progress(lines: Iterable[str], duration_seconds: float) -> dict[str, Any]:
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = str(line).strip().partition("=")
        if separator:
            values[key] = value
    out_time_us = _finite_int(values.get("out_time_us"), None)
    if out_time_us is None:
        out_time_us = _finite_int(values.get("out_time_ms"), 0)
    out_time_us = max(0, int(out_time_us or 0))
    processed_seconds = max(0.0, out_time_us / 1_000_000.0)
    progress = 0.0
    if duration_seconds > 0:
        progress = min(1.0, processed_seconds / duration_seconds)
    if values.get("progress") == "end":
        progress = 1.0
    return {
        "progress": progress,
        "processed_seconds": processed_seconds,
        "frame": max(0, int(_finite_int(values.get("frame"), 0) or 0)),
        "fps": max(0.0, float(_finite_float(values.get("fps"), 0.0) or 0.0)),
        "speed": str(values.get("speed") or ""),
        "state": str(values.get("progress") or "continue"),
    }
