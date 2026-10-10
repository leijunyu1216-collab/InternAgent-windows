"""Fixture for a node-scoped execution agent: its model calls carry node /
subtask identity, and its tool calls must NOT be converted to streaming."""


class ExecutionAgent:
    def __init__(self, node_id, subtask_id=None, task_id=None):
        self.node_id = node_id
        self.subtask_id = subtask_id
        self.task_id = task_id

    def send_redis_event(self, event_type, event_data):
        return True

    def plan_subtask(self, task):
        from openai.resources.chat.completions.completions import Completions
        return Completions.create(
            object(),
            model="deepseek-flash",
            messages=[{"role": "user", "content": f"怎么做：{task}"}],
            stream=False,
        )

    def call_tool_model(self, messages, tools):
        from openai.resources.chat.completions.completions import Completions
        return Completions.create(
            object(), model="deepseek-flash", messages=messages,
            tools=tools, stream=False,
        )
