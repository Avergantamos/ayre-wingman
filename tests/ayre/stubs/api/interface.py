from types import SimpleNamespace


class _Model:
    @classmethod
    def model_validate(cls, data):
        return SimpleNamespace(**data)


class CommandConfig(_Model):
    pass


class SettingsConfig(_Model):
    pass


class SkillConfig(_Model):
    pass


class WingmanInitializationError(Exception):
    pass
