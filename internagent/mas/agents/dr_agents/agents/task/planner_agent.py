import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agents.base_agent import BaseAgent
from models import get_model
from utils.logger import get_logger
from utils.prompt_loader import load_prompt
from tools import get_tool_manager

from typing import Any, Dict, List
import json



class PlannerAgent(BaseAgent):
    """
    规划器 Agent，负责将任务分解为子任务
    """
    
    def __init__(self, model: str = "deepseek-r1", tools: List[Dict[str, Any]] = None, tool_mode: str = "local", config=None, **model_kwargs):
        """
        初始化规划器 Agent
        
        Args:
            model: 使用的模型名称
            tools: 可用的工具列表
            tool_mode: 工具模式，'test'使用测试工具，其他值使用本地工具
            config: TaskWorkflowConfig配置对象
            **model_kwargs: 传递给模型的参数
        """
        super().__init__(model=model, tools=tools)
        self.model_instance = get_model(model, **model_kwargs)
        self.tool_manager = get_tool_manager(mode=tool_mode, config=config)
        self.tool_mode = tool_mode
        # 使用基类的 _logger，可以通过 _set_logger 动态更改
        self._set_logger(get_logger("PlannerAgent"))
        self.config = config or {}
        
        # 从配置加载prompt（如果配置中没有指定，使用默认值）
        self.prompt_template = load_prompt(
            self.config,
            default_name="PLANNER_PROMPT"
        )
        
        self.max_subtasks = self.config.get('max_subtasks', 2)
    
    def execute(self, input_data: Dict[str, Any]) -> str:
        """
        执行任务规划，将输入任务分解为子任务
        
        Args:
            input_data: 包含以下键的字典：
                - task: 任务内容
                - knowledge_info: 额外信息
                
        Returns:
            格式化的子任务列表（XML格式）
        """
        self._logger.info("开始执行任务规划")
        
        # 提取输入数据
        task = input_data.get("task", "")
        query = input_data.get("query","")
        knowledge_info = input_data.get("knowledge_info", "")
        file_path = input_data.get("file_path", "")

        if file_path:
            additional_info = self.tool_manager.call_tool("extract_document_content", document_path=file_path)
        else:
            additional_info = None

        tools = self.tool_manager.list_tools()
        tool_info_list = []
        for tool in tools:
            tool_info = self.tool_manager.get_simple_tool_info(tool)
            tool_info_list.append(tool_info)

        self._logger.info(f"任务内容: {task}")
        self._logger.info(f"知识信息: {knowledge_info}")
        self._logger.info(f"额外信息: {additional_info}")
        
        # 格式化 prompt
        formatted_prompt = self.prompt_template.format(
            task=task,
            query=query,
            knowledge_info=knowledge_info,
            additional_info=additional_info,
            max_subtasks = self.max_subtasks,
            tools=json.dumps(tool_info_list)
        )

        # print("formatted_prompt: ", formatted_prompt)
        
        subtasks = self._generate_subtasks(formatted_prompt)
        
        self._logger.info(f"子任务生成完成, 子任务: {subtasks}")
        return subtasks
    
    def _generate_subtasks(self, prompt: str) -> str:
        """
        生成子任务
        
        Args:
            prompt: 格式化的提示词
            
        Returns:
            子任务列表
        """
        try:
            # 调用模型生成子任务
            response = self.model_instance.generate(prompt)
            return response
        except Exception as e:
            # 如果模型调用失败，返回默认响应
            self._logger.error(f"模型调用失败: {e}")
            self._logger.warning("使用默认子任务响应")
            # Local patch: this fallback used to hand back 4 vague tasks no
            # matter what max_subtasks said, so a dead endpoint produced more
            # tool calls than a working one — and "搜索相关信息和数据" is
            # exactly the goal that gets a small model re-running the same
            # search until the budget runs out.
            fallback = ["分析任务需求并确定所需信息", "搜索相关信息和数据",
                        "处理和验证信息", "生成最终答案"]
            picked = fallback[:max(1, int(self.max_subtasks))]
            return "<tasks>\n" + "\n".join(
                f"<task>{t}</task>" for t in picked) + "\n</tasks>"
    
    def get_prompt_template(self) -> str:
        """
        获取当前使用的提示词模板
        
        Returns:
            提示词模板
        """
        return self.prompt_template
