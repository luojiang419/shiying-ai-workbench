# 上游与本地修改

- 来源：https://github.com/wyzborrero/ComfyUI-H3-FunControl
- 固定提交：d2a3faa7e29f45b8cb03685368c1243f08143910
- 代码许可证：Apache-2.0，见同目录 LICENSE。
- 本地修改：控制塔注册到 Comfy ModelPatcher 的附加模型中计入显存预算；采样结束、失败或取消后卸载控制塔并释放临时流。
- 本地采样生命周期内至少预留6GiB工作空间，避免Windows独立显存耗尽后的严重分页；finally恢复用户原预留值。节点类名加MiniMaxClone前缀，避免与原始诊断节点冲突。
- 模型权重单独部署，不包含在源码中，遵循原模型许可证。
