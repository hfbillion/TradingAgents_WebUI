"""记忆日志：每个决策的仅追加 markdown 记录，以及结算后的结果。"""

import contextlib  # 导入上下文管理器
import re  # 导入正则表达式
from pathlib import Path  # 导入路径操作

from tradingagents.agents.rating import parse_rating  # 导入评级解析函数
from tradingagents.dataflows.files import locked  # 导入文件锁定函数


class TradingMemoryLog:
    """交易决策和反思的仅追加 markdown 日志。"""

    # HTML 注释：不能出现在 LLM 散文输出中，作为硬分隔符是安全的
    _SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"
    # 预编译模式 — 避免在每次 load_entries() 调用时重新编译
    _DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)  # 决策正则
    _REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)  # 反思正则

    def __init__(self, config: dict = None):
        cfg = config or {}
        self._log_path = None  # 日志文件路径
        path = cfg.get("memory_log_path")
        if path:
            self._log_path = Path(path).expanduser()  # 展开用户目录
            self._log_path.parent.mkdir(parents=True, exist_ok=True)  # 创建目录
        # 已解析条目的可选上限。None 禁用轮换。
        self._max_entries = cfg.get("memory_log_max_entries")

    # --- 写入：运行记录其决策 ---

    def store_decision(
        self,
        ticker: str,  # 股票代码
        trade_date: str,  # 交易日期
        final_trade_decision: str,  # 最终交易决策
        rating: str | None = None,  # 评级（可选）
    ) -> None:
        """在 propagate() 结束时追加待处理条目。不调用 LLM。

        ``rating`` 是调用者拥有的决策自己的评级；
        如果没有，则从决策文本中读取。
        """
        if not self._log_path:
            return  # 如果没有日志路径，直接返回
        with locked(self._log_path):  # 锁定文件
            # 幂等性保护：快速原始文本扫描而不是完全解析。
            # 此股票和日期的任何条目都会阻止另一个条目（待处理或已结算）：
            # 结果落地后的重新运行否则会在过去上下文中和日志的每个聚合中
            # 将同一决策计算两次。
            if self._log_path.exists():
                raw = self._log_path.read_text(encoding="utf-8")
                for line in raw.splitlines():
                    if line.startswith(f"[{trade_date} | {ticker} |") and line.endswith("]"):
                        return  # 如果已存在，跳过
            rating = rating or parse_rating(final_trade_decision)  # 解析评级
            tag = f"[{trade_date} | {ticker} | {rating} | pending]"  # 创建标签
            entry = f"{tag}\n\nDECISION:\n{final_trade_decision}{self._SEPARATOR}"  # 创建条目
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(entry)  # 追加写入

    def settling(self, wait: bool = True):
        """一次只保留一个结算通道的日志，跨线程和进程。

        通道读取待处理条目，要求模型为每个条目进行反思并写入；
        两个通道同时会为相同的反思付费。
        这是与每次写入所取锁分开的单独锁。
        返回是否持有锁；使用 ``wait=False`` 不等待另一个通道。
        """
        if not self._log_path:
            return contextlib.nullcontext(True)  # 如果没有日志路径，返回空上下文
        return locked(f"{self._log_path}.settle", wait=wait)  # 返回锁定上下文

    # --- 读取 ---

    def load_entries(self) -> list[dict]:
        """从日志解析所有条目。返回字典列表。"""
        if not self._log_path or not self._log_path.exists():
            return []  # 如果日志不存在，返回空列表
        text = self._log_path.read_text(encoding="utf-8")
        raw_entries = [e.strip() for e in text.split(self._SEPARATOR) if e.strip()]  # 分割条目
        entries = []
        for raw in raw_entries:
            parsed = self._parse_entry(raw)  # 解析条目
            if parsed:
                entries.append(parsed)
        return entries

    def get_pending_entries(self) -> list[dict]:
        """返回状态为 pending 的条目，用于结算。"""
        return [e for e in self.load_entries() if e.get("pending")]

    def get_past_context(
        self, ticker: str, n_same: int = 5, n_cross: int = 3, as_of: str | None = None
    ) -> str:
        """返回格式化的过去上下文字符串，用于智能体提示注入。

        当给出 ``as_of`` (yyyy-mm-dd) 时，仅包含该日期之前已知道结果的教训
        — 仅当条目存储的解决日期（``resolved:...``）在该日期或之前时才保留条目
        ``as_of``。这使历史/回测运行不会从尚未发生的结果中学习（#1251）。
        ``as_of=None`` 禁用过滤器，因此实时运行和迁移前条目不受影响。
        """
        entries = [e for e in self.load_entries() if not e.get("pending")]  # 只获取已结算的条目
        if as_of is not None:
            # 过滤：只保留解决日期在 as_of 之前的条目
            entries = [e for e in entries if e.get("resolved") and e["resolved"] <= as_of]
        if not entries:
            return ""  # 如果没有条目，返回空字符串

        same, cross = [], []  # 同股票和跨股票的条目
        for e in reversed(entries):  # 从最新到最旧
            if len(same) >= n_same and len(cross) >= n_cross:
                break  # 如果已收集足够，停止
            if e["ticker"] == ticker and len(same) < n_same:
                same.append(e)  # 同股票条目
            elif e["ticker"] != ticker and len(cross) < n_cross:
                cross.append(e)  # 跨股票条目

        if not same and not cross:
            return ""  # 如果没有条目，返回空字符串

        parts = []
        if same:
            parts.append(f"Past analyses of {ticker} (most recent first):")  # 同股票分析
            parts.extend(self._format_full(e) for e in same)
        if cross:
            parts.append("Recent cross-ticker lessons:")  # 跨股票教训
            parts.extend(self._format_reflection_only(e) for e in cross)
        return "\n\n".join(parts)  # 返回格式化的上下文

    # --- 结算：记录决策的结果和反思 ---

    def update_with_outcome(
        self,
        ticker: str,  # 股票代码
        trade_date: str,  # 交易日期
        raw_return: float,  # 原始收益率
        alpha_return: float,  # Alpha 收益率
        holding_days: int,  # 持有天数
        reflection: str,  # 反思
        resolution_date: str | None = None,  # 解决日期
    ) -> bool:
        """使用原子写入替换待处理标签并追加 REFLECTION 部分。

        返回是否更新了条目：当决策不再待处理时返回 False
        （另一个通道先结算了它）。

        查找匹配 (trade_date, ticker) 的第一个待处理条目，
        用返回数字更新其标签（以及结果变为已知的 ``resolution_date``），
        并追加 REFLECTION 部分。使用临时文件 + os.replace()，
        以便写入中途崩溃永远不会损坏日志。
        """
        if not self._log_path or not self._log_path.exists():
            return False  # 如果日志不存在，返回 False
        with locked(self._log_path):  # 锁定文件
            text = self._log_path.read_text(encoding="utf-8")
            blocks = text.split(self._SEPARATOR)  # 分割块

            pending_prefix = f"[{trade_date} | {ticker} |"
            raw_pct = f"{raw_return:+.1%}"  # 格式化原始收益率
            alpha_pct = f"{alpha_return:+.1%}"  # 格式化 Alpha 收益率

            updated = False
            new_blocks = []
            for block in blocks:
                stripped = block.strip()
                if not stripped:
                    new_blocks.append(block)
                    continue

                lines = stripped.splitlines()
                tag_line = lines[0].strip()

                # 查找待处理条目
                if (
                    not updated
                    and tag_line.startswith(pending_prefix)
                    and tag_line.endswith("| pending]")
                ):
                    fields = [f.strip() for f in tag_line[1:-1].split("|")]
                    rating = fields[2]
                    new_tag = self._resolved_tag(
                        trade_date, ticker, rating, raw_pct, alpha_pct, holding_days, resolution_date
                    )
                    rest = "\n".join(lines[1:])
                    new_blocks.append(
                        f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{reflection}"
                    )
                    updated = True
                else:
                    new_blocks.append(block)

            if not updated:
                return False  # 如果没有更新，返回 False

            new_blocks = self._apply_rotation(new_blocks)  # 应用轮换
            new_text = self._SEPARATOR.join(new_blocks)
            tmp_path = self._log_path.with_suffix(".tmp")  # 创建临时文件
            tmp_path.write_text(new_text, encoding="utf-8")
            tmp_path.replace(self._log_path)  # 原子替换
            return True

    def batch_update_with_outcomes(self, updates: list[dict]) -> None:
        """在单次读取 + 原子写入中应用多个结果更新。

        updates 的每个元素必须具有键：ticker, trade_date,
        raw_return, alpha_return, holding_days, reflection。
        """
        if not self._log_path or not self._log_path.exists() or not updates:
            return  # 如果日志不存在或没有更新，直接返回
        with locked(self._log_path):  # 锁定文件
            text = self._log_path.read_text(encoding="utf-8")
            blocks = text.split(self._SEPARATOR)  # 分割块

            update_map = {(u["trade_date"], u["ticker"]): u for u in updates}  # 创建更新映射

            new_blocks = []
            for block in blocks:
                stripped = block.strip()
                if not stripped:
                    new_blocks.append(block)
                    continue

                lines = stripped.splitlines()
                tag_line = lines[0].strip()

                matched = False
                for (trade_date, ticker), upd in list(update_map.items()):
                    pending_prefix = f"[{trade_date} | {ticker} |"
                    if tag_line.startswith(pending_prefix) and tag_line.endswith("| pending]"):
                        fields = [f.strip() for f in tag_line[1:-1].split("|")]
                        rating = fields[2]
                        raw_pct = f"{upd['raw_return']:+.1%}"
                        alpha_pct = f"{upd['alpha_return']:+.1%}"
                        new_tag = self._resolved_tag(
                            trade_date, ticker, rating, raw_pct, alpha_pct,
                            upd["holding_days"], upd.get("resolution_date"),
                        )
                        rest = "\n".join(lines[1:])
                        new_blocks.append(
                            f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{upd['reflection']}"
                        )
                        del update_map[(trade_date, ticker)]  # 删除已处理的更新
                        matched = True
                        break

                if not matched:
                    new_blocks.append(block)

            new_blocks = self._apply_rotation(new_blocks)  # 应用轮换
            new_text = self._SEPARATOR.join(new_blocks)
            tmp_path = self._log_path.with_suffix(".tmp")  # 创建临时文件
            tmp_path.write_text(new_text, encoding="utf-8")
            tmp_path.replace(self._log_path)  # 原子替换

    # --- 辅助函数 ---

    @staticmethod
    def _resolved_tag(
        trade_date, ticker, rating, raw_pct, alpha_pct, holding_days, resolution_date
    ) -> str:
        """构建已解析条目标签，记录结果的已知日期。

        ``resolution_date``（用于返回的最后一个价格条的日期）
        是后续运行过滤的时间点截止（#1251）。
        不可用时省略，保留传统的 6 字段标签。
        """
        tag = f"[{trade_date} | {ticker} | {rating} | {raw_pct} | {alpha_pct} | {holding_days}d"
        if resolution_date:
            tag += f" | resolved:{resolution_date}"
        return tag + "]"

    def _apply_rotation(self, blocks: list[str]) -> list[str]:
        """当已解析块的数量超过 max_entries 时，删除最旧的已解析块。

        待处理块始终保留（它们代表未处理的工作）。
        当禁用轮换或低于上限时，返回 ``blocks`` 不变。
        """
        if not self._max_entries or self._max_entries <= 0:
            return blocks  # 如果未设置上限，直接返回

        # 通过解析标签行标记将每个块标记为 (kept, is_resolved)
        decisions = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                decisions.append((block, False))
                continue
            tag_line = stripped.splitlines()[0].strip()
            is_resolved = (
                tag_line.startswith("[")
                and tag_line.endswith("]")
                and not tag_line.endswith("| pending]")
            )
            decisions.append((block, is_resolved))

        resolved_count = sum(1 for _, r in decisions if r)  # 计算已解析块数量
        if resolved_count <= self._max_entries:
            return blocks  # 如果未超过上限，直接返回

        to_drop = resolved_count - self._max_entries  # 计算需要删除的数量
        kept: list[str] = []
        for block, is_resolved in decisions:
            if is_resolved and to_drop > 0:
                to_drop -= 1
                continue  # 跳过要删除的块
            kept.append(block)
        return kept

    def _parse_entry(self, raw: str) -> dict | None:
        """解析单个条目"""
        lines = raw.strip().splitlines()
        if not lines:
            return None
        tag_line = lines[0].strip()
        if not (tag_line.startswith("[") and tag_line.endswith("]")):
            return None
        fields = [f.strip() for f in tag_line[1:-1].split("|")]
        if len(fields) < 4:
            return None
        # 可选的尾随 "resolved:YYYY-MM-DD" 字段记录结果何时变为已知，用于时间点过滤（#1251）。
        resolved = None
        for f in fields[6:]:
            if f.startswith("resolved:"):
                resolved = f[len("resolved:"):].strip()
        entry = {
            "date": fields[0],  # 日期
            "ticker": fields[1],  # 股票代码
            "rating": fields[2],  # 评级
            "pending": fields[3] == "pending",  # 是否待处理
            "raw": fields[3] if fields[3] != "pending" else None,  # 原始收益率
            "alpha": fields[4] if len(fields) > 4 else None,  # Alpha 收益率
            "holding": fields[5] if len(fields) > 5 else None,  # 持有天数
            "resolved": resolved,  # 解决日期
        }
        body = "\n".join(lines[1:]).strip()
        decision_match = self._DECISION_RE.search(body)  # 提取决策
        reflection_match = self._REFLECTION_RE.search(body)  # 提取反思
        entry["decision"] = decision_match.group(1).strip() if decision_match else ""
        entry["reflection"] = reflection_match.group(1).strip() if reflection_match else ""
        return entry

    def _format_full(self, e: dict) -> str:
        """格式化完整条目（包括决策和反思）"""
        raw = e["raw"] or "n/a"
        alpha = e["alpha"] or "n/a"
        holding = e["holding"] or "n/a"
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {raw} | {alpha} | {holding}]"
        parts = [tag, f"DECISION:\n{e['decision']}"]
        if e["reflection"]:
            parts.append(f"REFLECTION:\n{e['reflection']}")
        return "\n\n".join(parts)

    def _format_reflection_only(self, e: dict) -> str:
        """格式化仅反思的条目（用于跨股票教训）"""
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {e['raw'] or 'n/a'}]"
        if e["reflection"]:
            return f"{tag}\n{e['reflection']}"
        text = e["decision"][:300]  # 截取前 300 个字符
        suffix = "..." if len(e["decision"]) > 300 else ""
        return f"{tag}\n{text}{suffix}"
