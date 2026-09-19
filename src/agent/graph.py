from langgraph.graph import StateGraph,START,END
from src.agent.state import AgentState
from src.agent.nodes.planner import build_planner_node
from src.agent.nodes.executor import build_executor_node
from src.interfaces.llm import BaseLLMProvider
from src.mcp_client.manager import MCPClientManager


def route_after_planner(state: AgentState) -> str:
    last_message = state['messages'][-1]
    
    # Safely check for tool_calls whether it's a dict or a LangChain Message object
    if isinstance(last_message, dict):
        tool_calls = last_message.get("tool_calls")
    else:
        tool_calls = getattr(last_message, "tool_calls", None)
        
    # If there are tool calls, route to the executor!
    if tool_calls:
        return 'executor'
        
    return END

def build_agent_graph(llm: BaseLLMProvider, manager: MCPClientManager):
    graph=StateGraph(AgentState)
    graph.add_node('planner',build_planner_node(llm))
    graph.add_node('executor',build_executor_node(manager))
                   
    graph.set_entry_point('planner')
    graph.add_conditional_edges('planner',route_after_planner,{'executor':'executor',END:END})
    graph.add_edge('executor','planner')

    return graph.compile()


