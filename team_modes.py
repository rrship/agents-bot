"""Mode identifiers shared by the workbench and saved local tasks."""

STANDARD = "standard"
OPC = "opc"


def job_mode(state):
    """Read a task's mode, including tasks saved before it was recorded explicitly."""
    if not isinstance(state, dict):
        raise ValueError("任务记录应为 JSON 对象。")
    has_routes = "opc_routes" in state
    has_roles = "opc_roles" in state
    if has_routes != has_roles:
        raise ValueError("OPC 任务缺少员工连接或岗位技能快照。")
    expected = OPC if has_routes else STANDARD
    mode = state.get("mode", expected)
    if mode not in (STANDARD, OPC):
        raise ValueError("未知的任务模式，请使用支持此任务的 Agents Bot 版本。")
    if mode != expected:
        raise ValueError("任务模式与保存的 OPC 岗位配置不一致。")
    if mode == OPC:
        roles, routes = state["opc_roles"], state["opc_routes"]
        if not isinstance(roles, dict) or not isinstance(routes, dict) or not routes or roles.keys() != routes.keys():
            raise ValueError("OPC 员工连接与岗位技能快照不匹配。")
    return mode
