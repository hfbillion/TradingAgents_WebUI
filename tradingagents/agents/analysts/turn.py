"""工具使用分析师的一轮，以及其工具轮次用完后的最后一轮。"""

import json  # 导入 JSON 模块

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # 导入消息类型

# 强制分析师生成最终报告的消息
WRAP_UP = (
    "You have used every tool round this report allows. Write your final report "
    "now from the tool results above, and say which data you could not retrieve."
)


def take_turn(prompt, llm, tools, messages):
    """分析师的回复及其报告，在调用工具时报告保持为空。

    被告知收尾时，它不使用工具回答：提供工具的模型可能无论被告知什么都会调用一个。
    它之前的调用及其结果作为文本到达它，因为提供商可能拒绝持有工具调用的历史
    当没有绑定时。
    """
    # 如果最后一条消息是 WRAP_UP 指令，则强制不使用工具
    if messages and isinstance(messages[-1], HumanMessage) and messages[-1].content == WRAP_UP:
        # 提示列出了它提供的工具；被告知无法调用工具的模型可能仍会尝试，
        # 并且某些提供商随后什么都不回答。
        prompt = prompt.partial(tool_names="none; your tool rounds are spent")
        result = (prompt | llm).invoke(_as_text(messages))  # 调用 LLM（不绑定工具）
        return result, result.content  # 返回结果和内容
    result = (prompt | llm.bind_tools(tools)).invoke(messages)  # 调用 LLM（绑定工具）
    return result, "" if result.tool_calls else result.content  # 返回结果和报告（如果有工具调用则为空）


def _as_text(messages):
    """将工具调用和工具结果写为纯消息的历史记录。"""
    written = []
    for message in messages:
        if isinstance(message, AIMessage) and message.tool_calls:
            # 处理 AI 消息中的工具调用
            said = message.content if isinstance(message.content, str) else ""
            calls = "; ".join(f"{c['name']}({json.dumps(c['args'], default=str)})" for c in message.tool_calls)
            written.append(AIMessage(content=f"{said}\n[Called {calls}]".strip()))
        elif isinstance(message, ToolMessage):
            # 处理工具消息
            written.append(HumanMessage(content=f"[{message.name or 'Tool'} returned]\n{message.content}"))
        else:
            # 其他消息保持不变
            written.append(message)
    return written
