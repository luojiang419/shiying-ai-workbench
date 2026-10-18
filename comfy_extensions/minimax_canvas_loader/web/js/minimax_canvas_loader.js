const { app } = window.comfyAPI.app;
const { api } = window.comfyAPI.api;

const PARAM_NAME = "minimax_workflow";
const WORKFLOWS = {
  sage: {
    path: "workflows/MiniMax-H3-SageAttention.json",
    title: "MiniMax-H3-SageAttention",
  },
  standard: {
    path: "workflows/MiniMax-H3-Standard.json",
    title: "MiniMax-H3-Standard",
  },
  turbo: {
    path: "workflows/MiniMax-H3-Turbo8Step.json",
    title: "MiniMax-H3-Turbo8Step",
  },
};

let requestedWorkflow = null;
let workflowDataPromise = null;
let isLoading = false;
let isLoaded = false;

function removeWorkflowParameter() {
  const url = new URL(window.location.href);
  url.searchParams.delete(PARAM_NAME);
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

async function loadRequestedWorkflow() {
  try {
    const workflow = await workflowDataPromise;
    isLoaded = true;
    await app.loadGraphData(workflow, true, false, requestedWorkflow.title);
    document.documentElement.dataset.minimaxWorkflowLoaded = requestedWorkflow.title;
    removeWorkflowParameter();
    requestAnimationFrame(() => requestAnimationFrame(() => app.canvas?.fitView?.()));
  } catch (error) {
    console.error("[MiniMax] 自动载入画布工作流失败", error);
    isLoading = false;
  }
}

app.registerExtension({
  name: "MiniMax.CanvasWorkflowLoader",

  setup() {
    const key = new URLSearchParams(window.location.search).get(PARAM_NAME);
    requestedWorkflow = WORKFLOWS[key] ?? null;
    if (!requestedWorkflow) return;

    workflowDataPromise = api.getUserData(requestedWorkflow.path).then(async (response) => {
      if (!response.ok) {
        throw new Error(`读取工作流失败：HTTP ${response.status}`);
      }
      return response.json();
    });
  },

  afterConfigureGraph() {
    if (!requestedWorkflow || !workflowDataPromise || isLoading || isLoaded) return;
    isLoading = true;
    window.setTimeout(loadRequestedWorkflow, 0);
  },
});
