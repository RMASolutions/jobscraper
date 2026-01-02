from abc import ABC, abstractmethod
from typing import TypedDict, Any, Annotated
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
import operator
import logging

logger = logging.getLogger(__name__)


class WorkflowState(TypedDict, total=False):
    """Base state for all workflows."""

    # Execution metadata
    execution_id: str
    workflow_name: str
    current_step: str

    # Data flow
    input_data: dict[str, Any]
    output_data: dict[str, Any]

    # Accumulated messages/logs
    messages: Annotated[list[str], operator.add]

    # Error handling
    error: str | None
    should_retry: bool

    # Custom data (workflow-specific)
    data: dict[str, Any]


class BaseWorkflow(ABC):
    """Abstract base class for all workflows."""

    def __init__(self, name: str):
        self.name = name
        self.graph: StateGraph | None = None
        self.checkpointer = MemorySaver()

    @abstractmethod
    def define_nodes(self) -> dict[str, callable]:
        """
        Define the nodes (steps) of the workflow.
        Returns a dict mapping node names to their handler functions.
        """
        pass

    @abstractmethod
    def define_edges(self) -> list[tuple]:
        """
        Define the edges (transitions) of the workflow.
        Returns a list of (from_node, to_node) tuples or
        (from_node, condition_func, {result: next_node}) for conditional edges.
        """
        pass

    @abstractmethod
    def get_entry_point(self) -> str:
        """Return the name of the entry node."""
        pass

    def build(self) -> StateGraph:
        """Build the LangGraph workflow."""
        self.graph = StateGraph(WorkflowState)

        # Add nodes
        nodes = self.define_nodes()
        for node_name, handler in nodes.items():
            self.graph.add_node(node_name, handler)

        # Set entry point
        self.graph.set_entry_point(self.get_entry_point())

        # Add edges
        for edge in self.define_edges():
            if len(edge) == 2:
                # Simple edge
                from_node, to_node = edge
                if to_node == "END":
                    self.graph.add_edge(from_node, END)
                else:
                    self.graph.add_edge(from_node, to_node)
            elif len(edge) == 3:
                # Conditional edge
                from_node, condition_func, mapping = edge
                # Convert "END" strings to END constant
                resolved_mapping = {
                    k: END if v == "END" else v for k, v in mapping.items()
                }
                self.graph.add_conditional_edges(from_node, condition_func, resolved_mapping)

        return self.graph

    def compile(self):
        """Compile the workflow graph."""
        if not self.graph:
            self.build()
        return self.graph.compile(checkpointer=self.checkpointer)

    async def run(
        self,
        input_data: dict[str, Any],
        execution_id: str,
        config: dict | None = None,
    ) -> dict[str, Any]:
        """Execute the workflow."""
        compiled = self.compile()

        initial_state: WorkflowState = {
            "execution_id": execution_id,
            "workflow_name": self.name,
            "current_step": self.get_entry_point(),
            "input_data": input_data,
            "output_data": {},
            "messages": [],
            "error": None,
            "should_retry": False,
            "data": {},
        }

        run_config = {"configurable": {"thread_id": execution_id}}
        if config:
            run_config.update(config)

        logger.info(f"Starting workflow '{self.name}' (execution_id={execution_id})")

        try:
            result = await compiled.ainvoke(initial_state, run_config)
            logger.info(f"Workflow '{self.name}' completed successfully")
            return result
        except Exception as e:
            logger.error(f"Workflow '{self.name}' failed: {e}")
            raise

    async def resume(
        self,
        execution_id: str,
        new_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resume a paused or failed workflow."""
        compiled = self.compile()
        config = {"configurable": {"thread_id": execution_id}}

        state = await compiled.aget_state(config)
        if new_input:
            state.values["input_data"].update(new_input)

        result = await compiled.ainvoke(None, config)
        return result
