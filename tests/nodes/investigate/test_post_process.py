import pytest
from app.nodes.investigate.execution.execute_actions import ActionExecutionResult
from app.nodes.investigate.processing.post_process import merge_evidence

@pytest.mark.parametrize(
    "action_name, data, expected_keys",
    [
        (
            "list_eks_pods",
            {
                "pods": [{"name": "fake-pod-1"}],
                "failing_pods": [{"name": "fake-pod-2"}],
                "high_restart_pods": [],
                "total_pods": 2
            },
            ["eks_pods", "eks_failing_pods", "eks_high_restart_pods", "eks_total_pods"]
        ),
        (
            "get_eks_events",
            {
                "warning_events": [{"message": "Back-off restarting failed container"}],
                "total_warning_count": 1
            },
            ["eks_events", "eks_total_warning_count"]
        ),
        (
            "list_eks_deployments",
            {
                "deployments": [{"name": "api"}],
                "degraded_deployments": [],
                "total_deployments": 1
            },
            ["eks_deployments", "eks_degraded_deployments", "eks_total_deployments"]
        ),
        (
            "get_eks_node_health",
            {
                "nodes": [{"name": "ip-10-0-0-1.ec2.internal"}],
                "not_ready_count": 0,
                "total_nodes": 1
            },
            ["eks_node_health", "eks_not_ready_count", "eks_total_nodes"]
        ),
        (
            "get_eks_pod_logs",
            {
                "logs": "Error: Connection refused...",
                "pod_name": "fake-pod-1",
                "namespace": "default"
            },
            ["eks_pod_logs", "eks_pod_logs_pod_name", "eks_pod_logs_namespace"]
        )
    ]
)
def test_merge_evidence_eks_tools(action_name, data, expected_keys):
    result = ActionExecutionResult(action_name=action_name, success=True, data=data)
    evidence = merge_evidence({}, {action_name: result})
    
    for key in expected_keys:
        assert key in evidence
