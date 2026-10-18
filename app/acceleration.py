"""Check the actual sampling model against settings captured at submission."""
from .config import (
    ACCELERATION_MODES,
    AFTERMIDNIGHT_PROFILE,
    HERETIC_TEXT_ENCODER_MODEL,
    NAUGHTYTIMES_LORA_MODEL,
    NAUGHTYTIMES_LORA_STRENGTH,
    NAUGHTYTIMES_PROFILE,
    NAUGHTYTIMES_PROFILES,
    NSFW_LORA_MODEL,
    NSFW_PROFILES,
    SAGE_ATTENTION_NODE_CLASS,
    TEXT_ENCODER_MODEL,
    TURBO_LORA_MODEL,
    TURBO_LORA_NODE_CLASS,
)


def validate_acceleration_prompt(
    prompt: dict,
    mode: str,
    *,
    nsfw_enabled: bool = False,
    nsfw_profile: str | None = None,
) -> None:
    if mode not in ACCELERATION_MODES:
        raise ValueError(f"不支持的生成加速方案: {mode}")
    if nsfw_enabled and nsfw_profile is None:
        nsfw_profile = AFTERMIDNIGHT_PROFILE
    if nsfw_profile is not None and nsfw_profile not in NSFW_PROFILES:
        raise ValueError(f"不支持的 NSFW 方案: {nsfw_profile}")
    use_turbo = mode in {"turbo_8step", "turbo_8step_sage"}
    nsfw_lora_names = {NSFW_LORA_MODEL, NAUGHTYTIMES_LORA_MODEL}
    prompt_uses_nsfw = any(
        node.get("class_type") == TURBO_LORA_NODE_CLASS
        and node.get("inputs", {}).get("lora_name") in nsfw_lora_names
        for node in prompt.values()
    )
    if nsfw_profile is None and prompt_uses_nsfw:
        raise ValueError("NSFW 自动加载校验失败：开关关闭但工作流包含 NSFW LoRA")
    if not use_turbo and nsfw_profile is None:
        return

    feature = "NSFW" if nsfw_profile is not None else "Turbo"

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(f"{feature} 自动加载校验失败：{message}；已阻止模型配置不匹配的生成")

    def linked_node(link):
        require(isinstance(link, list) and len(link) == 2 and link[1] == 0, "主模型连接无效")
        node = prompt.get(str(link[0]))
        require(isinstance(node, dict), "主模型连接指向不存在的节点")
        return node

    samplers = [node for node in prompt.values() if node.get("class_type") == "SamplerCustomAdvanced"]
    require(bool(samplers), "缺少采样节点")
    for sampler in samplers:
        inputs = sampler.get("inputs", {})
        guider = linked_node(inputs.get("guider"))
        require(guider.get("class_type") == "BasicGuider", "采样器未连接 BasicGuider")
        model = linked_node(guider.get("inputs", {}).get("model"))
        if model.get("class_type") == "MiniMaxCloneDepthControlApply":
            model = linked_node(model.get("inputs", {}).get("model"))
        for class_type in ("H3AIMDOResidencyLimiter", "H3MemoryOptimization"):
            if model.get("class_type") == class_type:
                model = linked_node(model.get("inputs", {}).get("model"))
        if mode in {"sage_attention", "turbo_8step_sage"}:
            require(model.get("class_type") == SAGE_ATTENTION_NODE_CLASS, "组合模式缺少 SageAttention")
            require(model.get("inputs", {}).get("sage_attention") == "auto", "SageAttention 未启用")
            model = linked_node(model.get("inputs", {}).get("model"))
        if nsfw_profile is not None:
            expected_lora = (
                NAUGHTYTIMES_LORA_MODEL
                if nsfw_profile in NAUGHTYTIMES_PROFILES
                else NSFW_LORA_MODEL
            )
            expected_strength = (
                NAUGHTYTIMES_LORA_STRENGTH
                if nsfw_profile in NAUGHTYTIMES_PROFILES
                else 1.0
            )
            require(model.get("class_type") == TURBO_LORA_NODE_CLASS, "NSFW LoRA 未连接到采样主模型")
            nsfw_inputs = model.get("inputs", {})
            require(nsfw_inputs.get("lora_name") == expected_lora, "NSFW LoRA 模型文件不匹配")
            require(nsfw_inputs.get("strength_model") == expected_strength, "NSFW LoRA 强度不匹配")
            model = linked_node(nsfw_inputs.get("model"))
        if use_turbo:
            require(model.get("class_type") == TURBO_LORA_NODE_CLASS, "Turbo LoRA 未连接到采样主模型")
            lora_inputs = model.get("inputs", {})
            require(lora_inputs.get("lora_name") == TURBO_LORA_MODEL, "Turbo LoRA 模型文件不匹配")
            require(lora_inputs.get("strength_model") == 1.0, "Turbo LoRA 强度必须为 1.0")
            model = linked_node(lora_inputs.get("model"))
        base = model
        require(base.get("class_type") == "UNETLoader", "LoRA 未连接基础模型")
        schedule = linked_node(inputs.get("sigmas")).get("inputs", {})
        expected_scheduler = (
            "beta"
            if use_turbo or nsfw_profile == AFTERMIDNIGHT_PROFILE
            else "simple"
        )
        require(schedule.get("scheduler") == expected_scheduler, f"调度器必须为 {expected_scheduler}")
        if use_turbo:
            require(schedule.get("steps") == 8, "Turbo 采样步数必须为 8")
        algorithm = linked_node(inputs.get("sampler")).get("inputs", {})
        require(algorithm.get("sampler_name") == "euler", "采样器必须为 Euler")
        if nsfw_profile is not None:
            clips = [node for node in prompt.values() if node.get("class_type") == "CLIPLoader"]
            require(bool(clips), "缺少文本编码器加载节点")
            expected_encoder = (
                HERETIC_TEXT_ENCODER_MODEL
                if nsfw_profile == NAUGHTYTIMES_PROFILE
                else TEXT_ENCODER_MODEL
            )
            require(
                all(node.get("inputs", {}).get("clip_name") == expected_encoder for node in clips),
                "文本编码器与 NSFW 方案不匹配",
            )
