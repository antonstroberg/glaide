#!/bin/sh
# Tells Glaide what the Claude Code session in one of its tabs is doing.
#
# Glaide starts Claude Code with a settings file whose hooks call this script as
#   hook.sh working | done | attention | error
# with the hook's JSON on stdin. It sends "<tab id>:<state>:<short text>" to the
# drawer, where the text is the first line of Claude's reply or the question it
# is asking.
#
# It does nothing outside a Glaide tab, and it must never print anything: Claude
# Code adds a hook's output to the conversation.
[ -n "$GLAIDE_TAB" ] && [ -n "$GLAIDE_BUS" ] || exit 0
input=$(cat 2>/dev/null)
# Events from inside a subagent are not the tab finishing.
case $input in *'"agent_id"'*) exit 0 ;; esac

text=
if [ "$1" != working ]; then
    # First non-empty line, markdown marks removed, at most 160 characters,
    # then escaped for the quoted string in the gdbus call below. The filter
    # must not contain an apostrophe: it sits inside a single-quoted sh string.
    text=$(printf %s "$input" | jq -r '
        ( if .tool_name == "ExitPlanMode" then "Plan ready for your review"
          else (.last_assistant_message // .message
                // (.tool_input.questions[0].question?) // .error // "") end
        ) | tostring
        | split("\n") | map(gsub("[*`]"; "") | gsub("^[#>\\s]+|\\s+$"; "")) | map(select(length > 0))
        | (.[0] // "") | gsub("\\s+"; " ")
        | if length > 160 then .[0:157] + "…" else . end
        | ([39] | implode) as $quote
        | gsub("\\\\"; "\\\\") | gsub($quote; "\\" + $quote)
    ' 2>/dev/null)
fi

gdbus call --session --dest "$GLAIDE_BUS" --object-path "/$(printf %s "$GLAIDE_BUS" | tr . /)" \
    --method org.gtk.Actions.Activate tab-state "[<'$GLAIDE_TAB:$1:$text'>]" '{}' \
    >/dev/null 2>&1
exit 0
