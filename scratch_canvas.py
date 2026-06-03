from textual.app import App, ComposeResult
from textual_canvas import Canvas
from textual.color import Color
from textual.events import Click

class MyCanvas(Canvas):
    def on_mount(self):
        self.draw_rectangle(0, 0, 10, 10, Color.parse("red"))

    def on_click(self, event: Click):
        print(f"Click at {event.x}, {event.y}")
        self.app.exit()

class MyApp(App):
    def compose(self) -> ComposeResult:
        yield MyCanvas(40, 40)

if __name__ == "__main__":
    app = MyApp()
    app.run()
