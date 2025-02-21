# PUEO PetaLinux Boot Image

The FIT images for PetaLinux used by PUEO are built using the
``petalinux.its`` configuration here. This configuration was
derived from the image in ``build/tmp/deploy/images/zynqmp-generic/``
(it's the only ``.its`` file there, the name is derived from
the Git tag).

The only changes were to the description and file names: this
image source expects to find files named

* ``linux.bin`` (gzipped Linux kernel)
* ``system-top.dtb`` (Device tree blob)
* ``pl.dtbo`` (Device tree overlay blob for PL peripherals)
* ``rootfs.cpio.gz`` (initramfs)

A script will be added here which takes a given ``image.ub`` and
updates a component of it.