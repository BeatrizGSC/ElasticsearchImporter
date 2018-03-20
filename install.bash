#!/bin/bash

function install_sqlite3 {
	echo "[ - Installing sqlite3... ]"
	echo "[ - Default sqlite3 version $(sqlite3 --version) and python module $(python -c 'import sqlite3; print(sqlite3.sqlite_version)')]"
	wget "https://www.sqlite.org/src/tarball/sqlite.tar.gz?r=release" -O sqlite.tar.gz &> /dev/null && tar -xzvf sqlite.tar.gz && rm -f sqlite.tar.gz
	cd sqlite
	sudo ./configure --enable-fts5 && sudo make && sudo make install
	sudo echo "/usr/local/lib" > /etc/ld.so.conf.d/sqlite.conf && ldconfig
	cd ..
	echo "[ - Installed sqlite3 version $(sqlite3 --version) and python module $(python -c 'import sqlite3; print(sqlite3.sqlite_version)')]"
}

function install_virtualenv_python {
	echo " ============================ "
	echo "[ - Installing virtualenv for $(python --version) ]"
	sudo pip install pip --upgrade
	virtualenv .env
	source .env/bin/activate
	pip install -r requirements.txt
	echo "[ - Checking tests... ]"
	python -m pytest
	echo "[ - Genarating geo-databases... in the ./db/ directory ]"
	python elasticImporter.py --regenerate_databases
	deactivate
	echo "[ - Remember to enable the virtualenv with 'source .env/bin/activate' if $(python --version) is to be used ]"
	echo " ============================ "
}

function install_virtualenv_pypy {
	echo "[ - Repository pypy version $(pypy --version) ]"
	sudo pypy -m ensurepip
	echo "[ - Installing PyPy in de local directory ]"
	wget https://bitbucket.org/squeaky/portable-pypy/downloads/pypy-5.10.0-linux_x86_64-portable.tar.bz2 -O pypy.tar.bz2 &> /dev/null
	mkdir pypy
	tar jxf pypy.tar.bz2 -C pypy --strip-components=1 && rm -f pypy.tar.bz2
	rm -f pypy/lib/libsqlite3.so.0
	echo "[ - Downloaded pypy version $(./pypy/bin/pypy --version) ]"
	./pypy/bin/virtualenv-pypy .envpypy
	source .envpypy/bin/activate
	echo "[ - Sqlite3 version $(./pypy/bin/pypy -c 'import sqlite3; print(sqlite3.sqlite_version)') ]"
	python -m pip install -r requirements.txt
	echo "[ - DO NOT run pytest with pypy, otherwise it will require huge amount of memory due to a strange leak from pypy+pytest ]"
	echo "[ - If needed, we recommend regenerating the databases using standard python, with the command. python elasticImporter.py --regenerate_databases ]"
	deactivate
	echo "[ - Remember to enable the virtualenv with 'source .envpypy/bin/activate' if pypy is to be used ]"
}

#UBUNTU & DEBIAN
if command -v apt-get &> /dev/null ; then
    echo "[ - Debian/Ubuntu based system detected ]"
	sudo apt-get update -y
	echo "[ - Removing sqlite3... ]"
	sudo apt-get remove -y sqlite3
	sudo apt-get purge -y sqlite3
	sudo apt-get install -y build-essential bzip2 git libbz2-dev libc6-dev libgdbm-dev libgeos-dev liblz-dev liblzma-dev libncurses5-dev libncursesw5-dev libreadline6 libreadline6-dev libsqlite3-dev libssl-dev lzma-dev python-dev python-pip python-software-properties python-virtualenv software-properties-common sqlite3 tcl tk-dev tk8.5-dev wget
	#install sqlite
	install_sqlite3
	#Reinstall python
	sudo apt-get remove -y python python3 python-dev
	sudo apt-get install -y --reinstall python2.7 python3 python-dev
	sudo apt-get install -y build-essential bzip2 git libbz2-dev libc6-dev libgdbm-dev libgeos-dev liblz-dev liblzma-dev libncurses5-dev libncursesw5-dev libreadline6 libreadline6-dev libsqlite3-dev libssl-dev lzma-dev python-dev python-pip python-software-properties python-virtualenv software-properties-common sqlite3 tcl tk-dev tk8.5-dev wget
#CENTOS & RHEL
elif yum --version &> /dev/null; then
	echo "[ - RHEL/CentOS based system detected ]"
	yum update -y
	yum groupinstall -y 'Development Tools'
	yum install -y sudo wget which git geos-devel python-pip python-virtualenv sqlite-devel tcl tcl-devel epel-release deltarpm python-numpy python-scipy python-matplotlib python-pandas
	sudo yum update -y
	#install sqlite
	install_sqlite3
#TO-DO: MAC
#ELSE EXIT
else
    echo "[ - Sorry, at the moment, the installation script only supports Ubuntu and CentOS systems, preferably Ubuntu 16.04 and Centos 7 ]"
    exit
fi

install_virtualenv_python

echo "[ - Installing pypy from repositories ]"
if command -v apt-get &> /dev/null ; then
	#install pypy
	sudo add-apt-repository -y ppa:pypy/ppa
	sudo apt-get update -y
	sudo apt-get install -y pypy pypy-dev
	sudo apt-get install -y --only-upgrade pypy
elif yum --version &> /dev/null; then
	yum install -y pypy pypy-devel geos-devel
else
	echo "[ - ERROR No PyPy for this OS. ]"
	exit
fi

install_virtualenv_pypy