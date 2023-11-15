import os
import logging

import termcolor


class ColorFormatter(logging.Formatter):
    __SPLIT_TOKEN = ":"
    __FORCE_COLOR_ENV_NAME = "FORCE_COLOR"
    __COLOR = "__color"
    __RESET = "__reset"

    # Note double `%%` sign for inline color and reset codes
    COLOR_FORMAT = (
        f"%(name)s: "
        f"%%({__COLOR})s%(levelname)s:%%({__RESET})s "
        f"%(message)s"
    )

    LEVEL_COLORS = {
        logging.DEBUG: {
            "color": "magenta",
            "on_color": None,
            "attrs": []
        },
        logging.INFO: {
            "color": "green",
            "on_color": None,
            "attrs": []
        },
        logging.WARNING: {
            "color": "yellow",
            "on_color": None,
            "attrs": ["bold"]
        },
        logging.ERROR: {
            "color": "red",
            "on_color": None,
            "attrs": ["bold"]
        },
        logging.CRITICAL: {
            "color": "light_red",
            "on_color": None,
            "attrs": ["bold"]
        },
    }

    def __init__(self, fmt=COLOR_FORMAT, *,
                 level_colors=LEVEL_COLORS, color=True):
        super().__init__(fmt=fmt)
        self.__level_colors = level_colors
        self.__color = color

    def formatMessage(self, record):
        # Prefer lower-case level name
        record.levelname = record.levelname.lower()

        s = super().formatMessage(record)

        return self.__format_with_color(s, record.levelno)

    def __format_with_color(self, s: str, level: int):
        if not self.__color:
            color_code, reset_code = '', ''
        else:
            color = self.__level_colors.get(level)

            old_value = os.environ.get(self.__FORCE_COLOR_ENV_NAME)

            # `colored` always return colors with env variable FORCE_COLOR
            os.environ[self.__FORCE_COLOR_ENV_NAME] = "1"

            # Use termcolor `colored` function to get
            # color and reset color codes
            color_code, reset_code = (
                termcolor.colored(self.__SPLIT_TOKEN, **color)
                         .split(self.__SPLIT_TOKEN)
            )

            if old_value is not None:
                os.environ[self.__FORCE_COLOR_ENV_NAME] = old_value
            else:
                del os.environ[self.__FORCE_COLOR_ENV_NAME]

        # Format message with inline color
        return s % {self.__COLOR: color_code, self.__RESET: reset_code}
