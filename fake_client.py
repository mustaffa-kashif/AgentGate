class FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, name, input, id="toolu_fake"):
        self.name = name
        self.input = input
        self.id = id


class FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeResponse:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, script):
        self.script = script
        self.calls = 0

    def create(self, **kwargs):
        if self.calls >= len(self.script):
            return FakeResponse([FakeTextBlock("done")], "end_turn")
        response = self.script[self.calls]
        self.calls += 1
        return response


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


def tool_call(name, args):
    return FakeResponse([FakeToolUseBlock(name, args)], "tool_use")


def text(message):
    return FakeResponse([FakeTextBlock(message)], "end_turn")
