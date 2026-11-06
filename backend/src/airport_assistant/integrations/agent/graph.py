"""LangGraph control flow, backed by domain-owned transactional checkpoints."""
import asyncio
from typing import TypedDict
from langgraph.graph import StateGraph,START,END
from langsmith import tracing_context
from airport_assistant.modules.agent.models import AgentError


class GraphState(TypedDict,total=False):
    identity:object
    run_id:object
    work:object
    result:object


class AgentGraph:
    def __init__(self,engine):
        self.engine=engine
        graph=StateGraph(GraphState)
        async def load(state):
            work=await engine.load(state['identity'],state['run_id'])
            return {'work':work,'result':work.result}
        async def plan(state):return {'result':await engine.plan(state['work'])}
        async def read(state):await engine.read(state['work']);return {}
        async def draft(state):return {'result':await engine.draft(state['work'])}
        def route(state):
            if state.get('result') is not None:return 'end'
            phase=state['work'].run['phase']
            if phase not in {'ready','tool','draft'}:raise AgentError(409,'AGENT_RUN_BUSY')
            return {'ready':'plan','tool':'read','draft':'draft'}[phase]
        graph.add_node('load',load);graph.add_node('plan',plan);graph.add_node('read',read);graph.add_node('draft',draft)
        graph.add_edge(START,'load')
        graph.add_conditional_edges('load',route,{'end':END,'plan':'plan','read':'read','draft':'draft'})
        graph.add_conditional_edges('plan',lambda s:'end' if s.get('result') is not None else 'load',{'end':END,'load':'load'})
        graph.add_edge('read','load');graph.add_edge('draft',END)
        # Do not serialize actor/private ephemeral data through a framework saver.
        # The engine commits typed metadata after each node; draft+receipt share SQL.
        self.graph=graph.compile()

    async def start(self,identity,request):return await self.engine.start(identity,request)

    async def resume(self,identity,id):
        # Explicit context overrides tracing environment variables as well.
        with tracing_context(enabled=False):
            try:
                async with asyncio.timeout(180):
                    result=await self.graph.ainvoke({'identity':identity,'run_id':id},
                        config={'recursion_limit':32,'max_concurrency':1,'callbacks':[]})
            except TimeoutError:raise AgentError(503,'AGENT_RUN_INTERRUPTED') from None
        return result['result']
