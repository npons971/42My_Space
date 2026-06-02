from textual.app import App, ComposeResult
from textual.containers import Container, Grid
from textual.widgets import Button, Static

class TestApp(App):
    CSS = """
    #wrapper { width: 100%; height: auto; align: center middle; background: red; padding: 1; }
    #status { width: auto; background: blue; }
    #grid {
        grid-size: 3 3; grid-rows: 3; grid-columns: 5;
        width: 15; height: auto; background: green;
    }
    Button { width: 5; height: 3; }
    """
    def compose(self) -> ComposeResult:
        with Container(id="wrapper"):
            yield Static("⭕ Tic-Tac-Toe", id="status")
            with Grid(id="grid"):
                for _ in range(9): yield Button("X")

if __name__ == "__main__":
    app = TestApp()
    app.run(headless=True)
