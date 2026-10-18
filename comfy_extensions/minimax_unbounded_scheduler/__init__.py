import torch

import comfy.samplers


class MiniMaxUnboundedBasicScheduler:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "scheduler": (comfy.samplers.SCHEDULER_NAMES,),
                "steps": ("INT", {"default": 20, "min": 1, "step": 1}),
                "denoise": (
                    "FLOAT",
                    {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01},
                ),
            }
        }

    RETURN_TYPES = ("SIGMAS",)
    FUNCTION = "get_sigmas"
    CATEGORY = "MiniMax/sampling"

    def get_sigmas(self, model, scheduler, steps, denoise):
        total_steps = steps
        if denoise < 1.0:
            if denoise <= 0.0:
                return (torch.FloatTensor([]),)
            total_steps = int(steps / denoise)

        sigmas = comfy.samplers.calculate_sigmas(
            model.get_model_object("model_sampling"), scheduler, total_steps
        ).cpu()
        return (sigmas[-(steps + 1) :],)


NODE_CLASS_MAPPINGS = {
    "MiniMaxUnboundedBasicScheduler": MiniMaxUnboundedBasicScheduler,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxUnboundedBasicScheduler": "MiniMax Basic Scheduler (No Step Limit)",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
