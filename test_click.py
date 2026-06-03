from textual.app import App, ComposeResult
from textual.containers import Container, Center
from textual_canvas import Canvas
from textual import events

class MyWidget(Container):
    def compose(self) -> ComposeResult:
        with Center():
            yield Canvas(40, 40, id="my_canvas")
            
    def on_click(self, event: events.Click):
        print(f"Widget Click: widget={event.widget}, x={event.x}, y={event.y}")

class MyApp(App):
    def compose(self) -> ComposeResult:
        yield MyWidget()

if __name__ == "__main__":
    app = MyApp()
    # We can't run the TUI, but we know Textual events.
    pass
