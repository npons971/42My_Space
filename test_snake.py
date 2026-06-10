from textual.app import App, ComposeResult
from ftmsg.games.snake import SnakeWidget, SnakeSession

class TestApp(App):
    def compose(self) -> ComposeResult:
        self.widget = SnakeWidget()
        yield self.widget

    def on_mount(self):
        # mock a state
        self.widget.state = {
            "score": 0,
            "phase": 0,
            "start_timer": 30,
            "snake": [(10, 10)],
            "food": (5, 5)
        }

if __name__ == "__main__":
    TestApp().run()
