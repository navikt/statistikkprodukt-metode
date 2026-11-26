from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        if self.editable_mode:
            return
        self.copy_file(
            str(Path(__file__).parent / "statistikkprodukt_register.md"),
            str(Path(self.build_lib) / "statistikkprodukt" / "statistikkprodukt_register.md"),
        )


setup(cmdclass={"build_py": BuildPy})
