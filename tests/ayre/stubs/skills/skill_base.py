import threading


def tool(name=None, description=None, summarize=True, wait_response=False):
    def deco(f):
        f._tool = dict(name=name or f.__name__, description=description)
        return f
    return deco


class Skill:
    props = {}
    gen_dir = "."

    def __init__(self, config=None, settings=None, wingman=None):
        self.config, self.settings, self.wingman = config, settings, wingman
        self.printr = type("P", (), {"print": staticmethod(lambda *a, **k: None)})()

    async def validate(self):
        return []

    async def unload(self):
        pass

    def retrieve_custom_property_value(self, prop, errors):
        return self.props.get(prop)

    def get_generated_files_dir(self):
        return self.gen_dir

    def threaded_execution(self, fn, *args):
        t = threading.Thread(target=lambda: __import__("asyncio").run(fn(*args)))
        t.start()
        return t

    async def llm_call(self, messages, tools=None):
        raise NotImplementedError
