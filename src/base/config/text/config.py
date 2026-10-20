from src.base.config.text.initializers import Initializers
from src.base.config.text.jwt import JWTTexts
from src.base.config.text.main import MainTexts
from src.base.config.text.middlewares import MiddlewareTexts


class TextConfigNew:
    main = MainTexts()
    jwt_texts = JWTTexts
    middleware = MiddlewareTexts()
    initializers = Initializers()
