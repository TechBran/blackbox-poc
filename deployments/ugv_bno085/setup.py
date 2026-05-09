from setuptools import setup

package_name = "ugv_bno085"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", ["launch/bno085.launch.py"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Brandon",
    maintainer_email="brandon@local",
    description="BNO085 IMU driver for UGV Beast (Game Rotation Vector, no mag).",
    license="MIT",
    entry_points={
        "console_scripts": [
            "imu_node = ugv_bno085.imu_node:main",
        ],
    },
)
