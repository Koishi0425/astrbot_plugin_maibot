"""Send editable score-completion instructions with QQ native input buttons."""

from astrbot.api import logger

from ..libraries.maimaidx_score import NOTE_TYPES
from ..libraries.maimaidx_score_recognition import DIFFICULTIES, RecognizedScore


async def score_completion_reply(event, score: RecognizedScore, reason: str):
    """Reply with known values and question marks, optionally using QQ buttons.

    Args:
        event: Requesting event, used only to reply to its own message.
        score: Observed or manually supplied score, including partial row cells.
        reason: Concise explanation of what the user needs to correct.

    Returns:
        A plain-text result, or None when the native QQ reply was sent.
    """
    parameters = [f"id={score.music_id or '?'}", f"ach={score.achievement or '?'}"]
    if score.difficulty is not None:
        parameters.append(f"diff={DIFFICULTIES[score.difficulty]}")
    for name in NOTE_TYPES:
        row = score.rows.get(name)
        cells = (
            [row.critical, row.perfect, row.great, row.good, row.miss]
            if row
            else score.partial_rows.get(name, [None] * 5)
        )
        if score.merged_perfect:
            cells = cells[1:]
        parameters.append(
            name.lower()
            + "="
            + "/".join("?" if value is None else str(value) for value in cells)
        )
    if score.dx_score is not None:
        maximum = f"/{score.max_dx_score}" if score.max_dx_score is not None else ""
        parameters.append(f"dx={score.dx_score}{maximum}")
    if score.layout.startswith("adx"):
        parameters.append("round=1")
    command = "mai识分 " + " ".join(parameters)
    order = (
        "P（含CP）/Great/Good/Miss" if score.merged_perfect else "CP/P/Great/Good/Miss"
    )
    description = f"{reason}\n将 ? 替换为实际数值后重新发送，已有数值也可以修改。\n判定顺序：{order}。"
    plain = description + "\n\n" + command
    get_platform = getattr(event, "get_platform_name", None)
    if callable(get_platform) and get_platform() == "qq_official":
        # The regular AstrBot message chain carries Markdown but no QQ keyboard.
        # Use the adapter's already-authenticated SDK only for this passive reply.
        try:
            import botpy.message

            source = event.message_obj.raw_message
            api = event.bot.api
            if isinstance(
                source, (botpy.message.GroupMessage, botpy.message.C2CMessage)
            ):
                fence = chr(96) * 3
                markdown = {
                    "content": description
                    + "\n\n"
                    + fence
                    + "\n"
                    + command
                    + "\n"
                    + fence
                    + "\n"
                }
                keyboard = {
                    "content": {
                        "rows": [
                            {
                                "buttons": [
                                    {
                                        "id": "maibot_score_edit",
                                        "render_data": {
                                            "label": "填写识分信息",
                                            "visited_label": "继续填写",
                                            "style": 1,
                                        },
                                        "action": {
                                            "type": 2,
                                            "permission": {"type": 2},
                                            "data": command,
                                            "enter": False,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                }
                payload = {
                    "msg_type": 2,
                    "msg_id": event.message_obj.message_id,
                    "msg_seq": 1,
                    "markdown": markdown,
                    "keyboard": keyboard,
                }
                if isinstance(source, botpy.message.GroupMessage):
                    response = await api.post_group_message(
                        group_openid=source.group_openid,
                        **payload,
                    )
                else:
                    response = await api.post_c2c_message(
                        openid=source.author.user_openid,
                        **payload,
                    )
                if isinstance(response, dict) and response.get("id"):
                    event.stop_event()
                    return None
                logger.warning("QQ score completion card returned no response")
        except Exception:
            logger.warning("QQ score completion card unavailable; falling back to text")
    result = event.plain_result(plain)
    # Keep the command editable even when the operator enabled global text-to-image.
    return result.use_t2i(False).use_markdown(False)
