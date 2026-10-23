# Imports of STE/Qt are intentionally deferred so the standalone mode is usable.
def getMetaData():
    return {}


def register(app):
    from .AIAssistant import AIAssistant
    return {"extension": AIAssistant(app)}
