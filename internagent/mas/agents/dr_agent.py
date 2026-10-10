"""
DR Agent for InternAgent

This module implements the DR (Discovery Research) Agent, which interfaces with
the DR workflow system to execute complex discovery research tasks.
"""

import logging
import sys
import os
from typing import Dict, Any

from .base_agent import BaseAgent, AgentExecutionError

logger = logging.getLogger(__name__)

# We'll use lazy/dynamic imports to avoid import errors at module load time
_workflow_class = None
_load_config_func = None
_get_config_func = None


def _get_workflow_class():
    """Lazy import of Workflow class to avoid import errors at module load time."""
    global _workflow_class
    if _workflow_class is not None:
        return _workflow_class
    current_dir = os.path.dirname(os.path.abspath(__file__))
    dr_agents_path = os.path.join(current_dir, "dr_agents")
    if dr_agents_path not in sys.path:
        sys.path.insert(0, dr_agents_path)
    try:
        from workflow.main import Workflow
        _workflow_class = Workflow
        return _workflow_class
    except ImportError as e:
        logger.error(f"Failed to import Workflow from dr_agents: {e}")
        return None


def _get_config_loaders():
    """Lazy import of config loader functions."""
    global _load_config_func, _get_config_func
    if _load_config_func is not None and _get_config_func is not None:
        return _load_config_func, _get_config_func
    current_dir = os.path.dirname(os.path.abspath(__file__))
    dr_agents_path = os.path.join(current_dir, "dr_agents")
    if dr_agents_path not in sys.path:
        sys.path.insert(0, dr_agents_path)
    try:
        from utils.config_loader import load_config, get_config
        _load_config_func, _get_config_func = load_config, get_config
        return _load_config_func, _get_config_func
    except ImportError as e:
        logger.error(f"Failed to import config loaders from dr_agents: {e}")
        return None, None


class DRAgent(BaseAgent):
    """
    DR Agent executes discovery research tasks using the DR workflow system.

    This agent serves as an interface between the InternAgent multi-agent system
    and the specialized DR (Discovery Research) workflow, enabling complex
    research discovery tasks to be executed within the broader agent framework.
    """
    
    def __init__(self, model, config: Dict[str, Any]):
        """
        Initialize the DR agent.
        
        Args:
            model: Language model to use
            config: Configuration dictionary containing DR-specific settings
        """
        super().__init__(model, config)
        
        # Set mode before loading DR workflow configuration
        self.mode = config.get("mode", "simple")
        
        # Load DR workflow configuration from config.yaml
        self.workflow_config = self._load_dr_config(config)
        
        # Initialize the DR workflow (using lazy import)
        self.workflow = None
        try:
            Workflow = _get_workflow_class()
            if Workflow is not None:
                self.workflow = Workflow(config=self.workflow_config)
                logger.info("DR workflow initialized successfully")
            else:
                logger.warning("Workflow class not available - import failed, DR agent will work in limited mode")
                # Create a placeholder to avoid failures
                # The agent can still be initialized but won't be able to execute
        except Exception as e:
            logger.warning(f"Failed to initialize DR workflow: {str(e)}, DR agent will work in limited mode")
            # Don't raise the exception, allow the agent to be created
    
    def _load_dr_config(self, agent_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Load DR workflow configuration from config.yaml file.
        
        Args:
            agent_config: Agent-specific configuration (can contain overrides)
        
        Returns:
            Merged configuration dictionary
        """
        # First, try to load from config.yaml in dr_agents folder
        workflow_config = None
        
        # Get config loader functions via lazy import
        load_config, get_config = _get_config_loaders()
        
        if load_config is not None:
            try:
                # Get the path to config.yaml in dr_agents folder
                current_dir = os.path.dirname(os.path.abspath(__file__))
                config_path = os.path.join(current_dir, "dr_agents", f"config_{self.mode}.yaml")
                
                if os.path.exists(config_path):
                    workflow_config = load_config(config_path)
                    logger.info(f"Loaded DR workflow config from: {config_path}")
                elif get_config is not None:
                    workflow_config = get_config()
                    logger.info("Using default DR workflow config")
            except Exception as e:
                logger.warning(f"Failed to load DR config.yaml: {e}")
        
        # If loading failed, use empty config or fallback
        if workflow_config is None:
            workflow_config = {}
            logger.warning("Using empty DR workflow config")
        
        # Merge with agent-specific config overrides
        if "workflow_config" in agent_config:
            workflow_config.update(agent_config["workflow_config"])
            logger.info("Applied agent config overrides to DR workflow config")
        
        return workflow_config
    
    async def execute(self, context: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute discovery research task using the DR workflow.
        
        Args:
            context: Dictionary containing:
                - task: The research task description
                - file_path: Optional file path for additional context
                - goal: Research goal information (optional)
                - iteration: Current iteration number (optional)
            params: Dictionary containing optional configuration overrides
                
        Returns:
            The workflow execution result (format depends on the workflow)
        """
        # Extract task information from context
        task = context.get("task", "")
        if not task:
            # Try alternative keys
            task = context.get("description", "") or context.get("goal", {}).get("description", "")
        
        if not task:
            raise AgentExecutionError("Task description is required for DR agent execution")
        
        # Extract optional file path
        file_path = context.get("file_path", None)
        
        # Check if workflow is initialized
        if self.workflow is None:
            logger.error("DR workflow is not initialized - cannot execute task")
            # Return a simple fallback response
            return f"Background research needed for: {task}\n\nNote: DR workflow is not available. Please provide background context manually."
        
        try:
            logger.info(f"DR Agent executing task: {task}")
            
            # Execute the DR workflow
            result = self.workflow.execute(task=task, file_path=file_path)
            
            logger.info("DR workflow execution completed successfully")
            
            # Return the result directly (user has modified return format)
            return result
            
        except Exception as e:
            logger.error(f"Error during DR workflow execution: {str(e)}")
            # Return a fallback response instead of raising error
            # Local patch: carry the real reason out of the except block. This
            # string is what the caller (and the CLI) prints, and without it a
            # planning failure, a bad key and a crashed tool all look exactly
            # the same on screen. The marker text the CLI greps for is kept.
            return (f"Background research for: {task}\n\n"
                    f"Note: DR workflow execution failed: {str(e)}. "
                    f"Please provide background context manually.")

