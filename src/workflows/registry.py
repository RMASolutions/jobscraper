from typing import Type
from .base import BaseWorkflow


class WorkflowRegistry:
    """Registry for workflow classes."""

    def __init__(self):
        self._workflows: dict[str, Type[BaseWorkflow]] = {}

    def register(self, name: str, workflow_class: Type[BaseWorkflow]) -> None:
        """Register a workflow class."""
        self._workflows[name] = workflow_class

    def get(self, name: str) -> Type[BaseWorkflow] | None:
        """Get a workflow class by name."""
        return self._workflows.get(name)

    def list(self) -> list[str]:
        """List all registered workflow names."""
        return list(self._workflows.keys())

    def create(self, name: str, **kwargs) -> BaseWorkflow:
        """Create an instance of a registered workflow."""
        workflow_class = self.get(name)
        if not workflow_class:
            raise ValueError(f"Workflow '{name}' not found in registry")
        return workflow_class(**kwargs)


# Global registry instance
workflow_registry = WorkflowRegistry()


def register_workflow(name: str):
    """Decorator to register a workflow class."""

    def decorator(cls: Type[BaseWorkflow]) -> Type[BaseWorkflow]:
        workflow_registry.register(name, cls)
        return cls

    return decorator
