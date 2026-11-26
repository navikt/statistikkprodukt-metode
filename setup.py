from pathlib import Path
import tomllib

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        if self.editable_mode:
            return
        config = tomllib.loads(
            (Path(__file__).parent / "pyproject.toml").read_text(encoding="utf-8")
        )
        requirement = config["tool"]["statistikkprodukt"]["r_package_requirement"]
        generated_config = (
            Path(self.build_lib)
            / "statistikkprodukt"
            / "_version_requirements.toml"
        )
        generated_config.write_text(
            "[tool.statistikkprodukt]\n"
            f'r_package_requirement = "{requirement}"\n',
            encoding="utf-8",
        )
        self.copy_file(
            str(Path(__file__).parent / "statistikkprodukt_register.md"),
            str(Path(self.build_lib) / "statistikkprodukt" / "statistikkprodukt_register.md"),
        )


setup(cmdclass={"build_py": BuildPy})
