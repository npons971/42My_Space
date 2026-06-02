from textual.app import App, ComposeResult
from textual.containers import Container, Grid, Center, Middle
from textual.widgets import Button

class TestApp(App):
    CSS = """
    Screen { align: center middle; }
    #wrapper { width: 100%; height: auto; align: center middle; background: red; }
    #grid {
        grid-size: 3 3; grid-rows: 3; grid-columns: 5;
        width: auto; height: auto; background: green;
    }
    Button { width: 5; height: 3; }
    """
    def compose(self) -> ComposeResult:
        with Container(id="wrapper"):
            with Center():
                with Grid(id="grid"):
                    for _ in range(9): yield Button("X")

if __name__ == "__main__":
    app = TestApp()
    app.run(headless=True)
