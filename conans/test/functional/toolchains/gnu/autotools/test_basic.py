import os
import platform
import textwrap
import time
import re

import pytest

from conan.tools.env.environment import environment_wrap_command
from conans.model.recipe_ref import RecipeReference
from conans.test.assets.autotools import gen_makefile_am, gen_configure_ac, gen_makefile
from conans.test.assets.sources import gen_function_cpp
from conans.test.functional.utils import check_exe_run
from conans.test.utils.tools import TestClient, TurboTestClient
from conans.util.files import touch


@pytest.mark.skipif(platform.system() not in ["Linux", "Darwin"], reason="Requires Autotools")
@pytest.mark.tool("autotools")
def test_autotools():
    client = TestClient(path_with_spaces=False)
    client.run("new cmake_lib -d name=hello -d version=0.1")
    client.run("create .")

    main = gen_function_cpp(name="main", includes=["hello"], calls=["hello"])
    makefile_am = gen_makefile_am(main="main", main_srcs="main.cpp")
    configure_ac = gen_configure_ac()

    conanfile = textwrap.dedent("""
        from conan import ConanFile
        from conan.tools.gnu import Autotools

        class TestConan(ConanFile):
            requires = "hello/0.1"
            settings = "os", "compiler", "arch", "build_type"
            exports_sources = "configure.ac", "Makefile.am", "main.cpp"
            generators = "AutotoolsDeps", "AutotoolsToolchain"

            def build(self):
                self.run("aclocal")
                self.run("autoconf")
                self.run("automake --add-missing --foreign")
                autotools = Autotools(self)
                autotools.configure()
                autotools.make()
        """)

    client.save({"conanfile.py": conanfile,
                 "configure.ac": configure_ac,
                 "Makefile.am": makefile_am,
                 "main.cpp": main}, clean_first=True)
    client.run("install .")
    client.run("build .")
    client.run_command("./main")
    cxx11_abi = 1 if platform.system() == "Linux" else None
    check_exe_run(client.out, "main", "gcc", None, "Release", "x86_64", None, cxx11_abi=cxx11_abi)
    assert "hello/0.1: Hello World Release!" in client.out


def build_windows_subsystem(profile, make_program):
    """ The AutotoolsDeps can be used also in pure Makefiles, if the makefiles follow
    the Autotools conventions
    """
    # FIXME: cygwin in CI (my local machine works) seems broken for path with spaces
    client = TestClient(path_with_spaces=False)
    client.run("new cmake_lib -d name=hello -d version=0.1")
    # TODO: Test Windows subsystems in CMake, at least msys is broken
    os.rename(os.path.join(client.current_folder, "test_package"),
              os.path.join(client.current_folder, "test_package2"))
    client.save({"profile": profile})
    client.run("create . --profile=profile")

    main = gen_function_cpp(name="main", includes=["hello"], calls=["hello"])
    makefile = gen_makefile(apps=["app"])

    conanfile = textwrap.dedent("""
        from conan import ConanFile
        from conan.tools.gnu import AutotoolsToolchain, Autotools, AutotoolsDeps

        class TestConan(ConanFile):
            requires = "hello/0.1"
            settings = "os", "compiler", "arch", "build_type"
            exports_sources = "Makefile"
            generators = "AutotoolsDeps", "AutotoolsToolchain"

            def build(self):
                autotools = Autotools(self)
                autotools.make()
        """)
    client.save({"app.cpp": main,
                 "Makefile": makefile,
                 "conanfile.py": conanfile,
                 "profile": profile}, clean_first=True)

    client.run("install . --profile=profile")
    cmd = environment_wrap_command(["conanbuildenv",
                                    "conanautotoolstoolchain",
                                    "conanautotoolsdeps"], make_program, cwd=client.current_folder)
    client.run_command(cmd)
    client.run_command("app")
    # TODO: fill compiler version when ready
    check_exe_run(client.out, "main", "gcc", None, "Release", "x86_64", None)
    assert "hello/0.1: Hello World Release!" in client.out

    client.save({"app.cpp": gen_function_cpp(name="main", msg="main2",
                                             includes=["hello"], calls=["hello"])})
    # Make sure it is newer
    t = time.time() + 1
    touch(os.path.join(client.current_folder, "app.cpp"), (t, t))

    client.run("build . --profile=profile")
    client.run_command("app")
    # TODO: fill compiler version when ready
    check_exe_run(client.out, "main2", "gcc", None, "Release", "x86_64", None, cxx11_abi=0)
    assert "hello/0.1: Hello World Release!" in client.out
    return client.out


@pytest.mark.tool("cygwin")
@pytest.mark.skipif(platform.system() != "Windows", reason="Needs windows")
def test_autotoolsdeps_cygwin():
    gcc = textwrap.dedent("""
        [settings]
        os=Windows
        os.subsystem=cygwin
        compiler=gcc
        compiler.version=4.9
        compiler.libcxx=libstdc++
        arch=x86_64
        build_type=Release
        """)
    out = build_windows_subsystem(gcc, make_program="make")
    assert "__MSYS__" not in out
    assert "MINGW" not in out
    assert "main2 __CYGWIN__1" in out


@pytest.mark.tool("mingw64")
@pytest.mark.skipif(platform.system() != "Windows", reason="Needs windows")
def test_autotoolsdeps_mingw_msys():
    gcc = textwrap.dedent("""
        [settings]
        os=Windows
        compiler=gcc
        compiler.version=4.9
        compiler.libcxx=libstdc++
        arch=x86_64
        build_type=Release
        """)
    out = build_windows_subsystem(gcc, make_program="mingw32-make")
    assert "__MSYS__" not in out
    assert "main2 __MINGW64__1" in out


@pytest.mark.tool("msys2")
@pytest.mark.skipif(platform.system() != "Windows", reason="Needs windows")
def test_autotoolsdeps_msys():
    gcc = textwrap.dedent("""
        [settings]
        os=Windows
        os.subsystem=msys2
        compiler=gcc
        compiler.version=4.9
        compiler.libcxx=libstdc++
        arch=x86_64
        build_type=Release
        """)
    out = build_windows_subsystem(gcc, make_program="make")
    # Msys2 is a rewrite of Msys, using Cygwin
    assert "MINGW" not in out
    assert "main2 __MSYS__1" in out
    assert "main2 __CYGWIN__1" in out


@pytest.mark.skipif(platform.system() not in ["Linux", "Darwin"], reason="Requires Autotools")
@pytest.mark.tool("autotools")
def test_install_output_directories():
    """
    If we change the libdirs of the cpp.package, as we are doing cmake.install, the output directory
    for the libraries is changed
    """
    client = TurboTestClient(path_with_spaces=False)
    client.run("new cmake_lib -d name=hello -d version=1.0")
    client.run("create .")
    consumer_conanfile = textwrap.dedent("""
        from conan import ConanFile
        from conan.tools.gnu import Autotools

        class TestConan(ConanFile):
            requires = "hello/1.0"
            settings = "os", "compiler", "arch", "build_type"
            exports_sources = "configure.ac", "Makefile.am", "main.cpp", "consumer.h"
            generators = "AutotoolsDeps", "AutotoolsToolchain"

            def layout(self):
                self.folders.build = "."
                self.cpp.package.bindirs = ["mybin"]

            def build(self):
                self.run("aclocal")
                self.run("autoconf")
                self.run("automake --add-missing --foreign")
                autotools = Autotools(self)
                autotools.configure()
                autotools.make()
                autotools.install()
    """)

    main = gen_function_cpp(name="main", includes=["hello"], calls=["hello"])
    makefile_am = gen_makefile_am(main="main", main_srcs="main.cpp")
    configure_ac = gen_configure_ac()
    client.save({"conanfile.py": consumer_conanfile,
                 "configure.ac": configure_ac,
                 "Makefile.am": makefile_am,
                 "main.cpp": main}, clean_first=True)
    ref = RecipeReference.loads("zlib/1.2.11")
    pref = client.create(ref, conanfile=consumer_conanfile)
    p_folder = client.get_latest_pkg_layout(pref).package()
    assert os.path.exists(os.path.join(p_folder, "mybin", "main"))
    assert not os.path.exists(os.path.join(p_folder, "bin"))


@pytest.mark.skipif(platform.system() not in ["Linux", "Darwin"], reason="Requires Autotools")
@pytest.mark.tool("autotools")
def test_autotools_with_pkgconfigdeps():
    client = TestClient(path_with_spaces=False)
    client.run("new cmake_lib -d name=hello -d version=1.0")
    client.run("create .")

    consumer_conanfile = textwrap.dedent("""
        [requires]
        hello/1.0
        [generators]
        AutotoolsToolchain
        PkgConfigDeps
    """)
    client.save({"conanfile.txt": consumer_conanfile}, clean_first=True)
    client.run("install .")

    client.run_command(". ./conanautotoolstoolchain.sh && "
                       "pkg-config --cflags hello && "
                       "pkg-config --libs-only-l hello && "
                       "pkg-config --libs-only-L --libs-only-other hello")

    assert re.search("I.*/p/include", str(client.out))
    assert "-lhello" in client.out
    assert re.search("L.*/p/lib", str(client.out))


@pytest.mark.skipif(platform.system() not in ["Linux", "Darwin"], reason="Requires Autotools")
@pytest.mark.tool("autotools")
def test_autotools_option_checking():
    # https://github.com/conan-io/conan/issues/11265
    client = TestClient(path_with_spaces=False)
    client.run("new autotools_lib -d name=mylib -d version=1.0")
    conanfile = textwrap.dedent("""
        import os

        from conan import ConanFile
        from conan.tools.gnu import AutotoolsToolchain, Autotools
        from conan.tools.layout import basic_layout
        from conan.tools.build import cross_building


        class MylibTestConan(ConanFile):
            settings = "os", "compiler", "build_type", "arch"
            generators = "AutotoolsDeps"

            def requirements(self):
                self.requires(self.tested_reference_str)

            def generate(self):
                at_toolchain = AutotoolsToolchain(self)
                # we override the default shared/static flags here
                at_toolchain.configure_args = ['--enable-option-checking=fatal']
                at_toolchain.generate()

            def build(self):
                autotools = Autotools(self)
                autotools.autoreconf()
                autotools.configure()
                autotools.make()

            def layout(self):
                basic_layout(self)

            def test(self):
                if not cross_building(self):
                    cmd = os.path.join(self.cpp.build.bindirs[0], "main")
                    self.run(cmd, env="conanrun")
            """)

    client.save({"test_package/conanfile.py": conanfile})
    client.run("create . -tf=None")

    # check that the shared flags are not added to the exe's configure, making it fail
    client.run("test test_package mylib/1.0@")
    assert "configure: error: unrecognized options: --disable-shared, --enable-static, --with-pic" \
           not in client.out


@pytest.mark.skipif(platform.system() not in ["Linux", "Darwin"], reason="Requires Autotools")
@pytest.mark.tool("autotools")
def test_autotools_arguments_override():
    client = TestClient(path_with_spaces=False)
    client.run("new autotools_lib -d name=mylib -d version=1.0")
    conanfile = textwrap.dedent("""
        import os

        from conan import ConanFile
        from conan.tools.gnu import AutotoolsToolchain, Autotools
        from conan.tools.layout import basic_layout


        class MyLibConan(ConanFile):
            name = "mylib"
            version = "1.0"

            # Binary configuration
            settings = "os", "compiler", "build_type", "arch"

            exports_sources = "configure.ac", "Makefile.am", "src/*"

            def config_options(self):
                if self.settings.os == "Windows":
                    del self.options.fPIC

            def layout(self):
                basic_layout(self)

            def generate(self):
                at_toolchain = AutotoolsToolchain(self)
                at_toolchain.configure_args = ['--disable-shared']
                at_toolchain.make_args = ['--warn-undefined-variables']
                at_toolchain.autoreconf_args = ['--verbose']
                at_toolchain.generate()

            def build(self):
                autotools = Autotools(self)
                autotools.autoreconf(args=['--install'])
                autotools.configure(args=['--prefix=/', '--libdir=${prefix}/customlibfolder',
                                          '--includedir=${prefix}/customincludefolder',
                                          '--pdfdir=${prefix}/res'])
                autotools.make(args=['--keep-going'])

            def package(self):
                autotools = Autotools(self)
                autotools.install(args=['DESTDIR={}/somefolder'.format(self.package_folder)])

            def package_info(self):
                self.cpp_info.libs = ["mylib"]
                self.cpp_info.libdirs = ["somefolder/customlibfolder"]
                self.cpp_info.includedirs = ["somefolder/customincludefolder"]
        """)
    #client.run("config set log.print_run_commands=1")
    client.save({"conanfile.py": conanfile})
    client.run("create . -tf=None")

    # autoreconf args --force that is default should not be there
    assert "--force" not in client.out
    assert "--install" in client.out

    package_id = client.created_package_id("mylib/1.0")
    ref = RecipeReference.loads("mylib/1.0")
    pref = client.get_latest_package_reference(ref, package_id)

    # we override the default DESTDIR in the install
    assert re.search("^.*make install .*DESTDIR=(.*)/somefolder.*$", str(client.out), re.MULTILINE)

    # we did override the default install args
    for arg in ['--bindir=${prefix}/bin', '--sbindir=${prefix}/bin',
                '--libdir=${prefix}/lib', '--includedir=${prefix}/include',
                '--oldincludedir=${prefix}/include', '--datarootdir=${prefix}/res']:
        assert arg not in client.out

    # and use our custom arguments
    for arg in ['--prefix=/', '--libdir=${prefix}/customlibfolder',
                '--includedir=${prefix}/customincludefolder', '--pdfdir=${prefix}/res']:
        assert arg in client.out

    # check the other arguments we set are there
    assert "--disable-shared" in client.out
    assert "--warn-undefined-variables" in client.out
    assert "--verbose" in client.out
    assert "--keep-going" in client.out

    client.run("test test_package mylib/1.0@")
    assert "mylib/1.0: Hello World Release!" in client.out
