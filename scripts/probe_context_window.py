"""上下文窗口探测脚本（spec F30）。

对目标模型发逐步增长的 prompt 直到 provider 返回 prompt_too_long，二分逼近边界。
产出的数字是经验下界（受系统提示、工具定义、provider 预留输出影响），由开发者
手工填入 mewcode/context/capabilities.py 并提交——不自动回填。

不在 Agent 主流程、不被 import、不被 F29 引用（F30）。
依赖有效 API key 与网络，由开发者手动运行。
"""

import argparse
import sys

# 探测用的填充 token 单位（保守按字符估算，与 ESTIMATE_CHARS_PER_TOKEN 一致）
_CHARS_PER_TOKEN = 3.5


def _build_provider(
    protocol: str, model: str, base_url: str | None, api_key: str | None
):
    """按协议构造 provider 适配器（复用 mewcode.provider，探测路径独立于 Agent）。"""
    from mewcode.config.schema import ProviderConfig
    from mewcode.provider.base import new_provider

    cfg = ProviderConfig(
        name="probe",
        protocol=protocol,
        model=model,
        base_url=base_url,
        api_key=api_key or "",
    )
    return new_provider(cfg)


def _fill_prompt(token_estimate: int) -> str:
    """构造约 token_estimate 个 token 的填充文本（按字符/3.5 估算）。"""
    char_count = int(token_estimate * _CHARS_PER_TOKEN)
    return "x " * (char_count // 2)


async def _probe_once(provider, token_estimate: int) -> tuple[bool, str | None]:
    """发一次填充 prompt，返回 (是否超长 PTL, 错误信息)。"""
    from mewcode.llm import PromptTooLongError
    from mewcode.prompt.assembler import PromptPayload
    from mewcode.provider.base import Message

    payload = PromptPayload(
        stable_prompt="probe",
        env_segment="",
        messages=[Message(role="user", content=_fill_prompt(token_estimate))],
        tools=None,
    )
    async for se in provider.stream(payload):
        if se.err is not None:
            if isinstance(se.err, PromptTooLongError):
                return (True, str(se.err))
            return (False, str(se.err))
        if se.done:
            return (False, None)
    return (False, None)


async def _binary_probe(provider, lo: int, hi: int, max_iter: int = 20) -> int:
    """二分逼近 PTL 边界，返回经验下界 token 数。"""
    for _ in range(max_iter):
        if hi - lo <= 1:
            break
        mid = (lo + hi) // 2
        too_long, _err = await _probe_once(provider, mid)
        if too_long:
            hi = mid
        else:
            lo = mid
        print(
            f"  探测 {mid} tokens → {'超长' if too_long else '通过'}", file=sys.stderr
        )
    return lo


async def _run(
    protocol: str, model: str, base_url: str | None, api_key: str | None
) -> int:
    provider = _build_provider(protocol, model, base_url, api_key)
    print(f"探测 {protocol}/{model} 的上下文窗口边界...", file=sys.stderr)
    # 先指数增长找上界，再二分
    hi = 1000
    while True:
        too_long, _ = await _probe_once(provider, hi)
        if too_long:
            break
        hi *= 2
        if hi > 10_000_000:
            print("已达 10M 仍未超长，终止", file=sys.stderr)
            return hi
    lower = await _binary_probe(provider, hi // 2, hi)
    print(lower)
    print(
        f"\n# 经验下界（受系统提示/工具/预留输出影响），手工填入 "
        f"mewcode/context/capabilities.py：\n"
        f'#   "{model}": {lower},  # 来源：probe_context_window.py 探测',
        file=sys.stderr,
    )
    return lower


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="probe_context_window.py",
        description="二分逼近目标模型的上下文窗口边界（经验下界，手工回填能力表）",
    )
    parser.add_argument("--protocol", required=True, choices=["anthropic", "openai"])
    parser.add_argument("--model", required=True, help="目标模型名")
    parser.add_argument("--base-url", default=None, help="自定义 base url（兼容代理）")
    parser.add_argument("--api-key", default=None, help="API key（也可用环境变量）")
    args = parser.parse_args()

    import asyncio

    try:
        asyncio.run(_run(args.protocol, args.model, args.base_url, args.api_key))
    except Exception as e:  # noqa: BLE001 — 探测脚本顶层兜底，给清晰错误退出
        print(f"探测失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
