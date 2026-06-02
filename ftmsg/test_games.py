from textual.app import App, ComposeResult
from ftmsg.games.widgets import HangmanWidget
from ftmsg.tui import WordRaceWidget, FtMsgApp

class TestApp(App):
    def compose(self) -> ComposeResult:
        app_mock = FtMsgApp()
        yield HangmanWidget(app_mock)
        yield WordRaceWidget(app_mock)

if __name__ == "__main__":
    app = TestApp()
    app.run(headless=True)
