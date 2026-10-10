"""Fixture standing in for the real GlobalPlannerAgent, so _inspect_caller can
be exercised without importing the DR stack (or spending API credits).

Lives under a path containing `dr_agents/` and is named after a real module —
those two facts are all the stack walk keys on. The nesting mirrors the real
one: plan() holds idx/max_iter, execute_one_step receives them as
current_iter/max_iter, and _generate_plan is the frame that issues the call.
"""


class GlobalPlannerAgent:
    def __init__(self, node_id=None, subtask_id=None, task_id=None):
        self.node_id = node_id
        self.subtask_id = subtask_id
        self.task_id = task_id

    def send_redis_event(self, event_type, event_data):
        return True

    def _generate_plan(self, graph, input_data):
        from openai.resources.chat.completions.completions import Completions
        return Completions.create(
            object(),
            model="deepseek-flash",
            messages=[{"role": "user", "content": "请规划研究步骤"}],
            stream=False,
        )

    def execute_one_step(self, graph, input_data, current_iter=None, max_iter=None):
        return self._generate_plan(graph, input_data)

    def plan(self, input_data, max_iter=10):
        idx = 3
        return self.execute_one_step("{}", input_data, current_iter=idx,
                                     max_iter=max_iter)
