#!/usr/bin/env python
# -*- coding: utf-8 -*-

from langgraph.graph import MessagesState, StateGraph, END
from langgraph.prebuilt import ToolNode

class AgentState(MessagesState):
    pass

def create_agent_graph(llm, tools, debug=False):
    model_with_tools = llm.bind_tools(tools)

    async def call_model(state: AgentState):
        response = await model_with_tools.ainvoke(state["messages"])
        return {"messages": [response]}

    async def should_continue(state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        if not hasattr(last_message, 'tool_calls') or len(last_message.tool_calls) <= 0:
            return "respond"
        else:
            return "continue"

    workflow = StateGraph(AgentState)

    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(tools))

    workflow.set_entry_point("agent")

    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "continue": "tools",
            "respond": END,
        },
    )

    workflow.add_edge("tools", "agent")
    graph = workflow.compile(debug=debug)

    return graph
