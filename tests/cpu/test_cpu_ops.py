import unittest
import copy
import torch
import torch.nn as nn
import torch.nn.functional as F
import random
import itertools
import intel_extension_for_pytorch as ipex
from common_utils import TestCase
import torch.autograd.functional as autogradF
from copy import deepcopy
import intel_extension_for_pytorch._C as core

try:
    import torchvision

    HAS_TORCHVISION = True
except ImportError:
    HAS_TORCHVISION = False
skipIfNoTorchVision = unittest.skipIf(not HAS_TORCHVISION, "no torchvision")

bn_m = {1: nn.BatchNorm1d, 2: nn.BatchNorm2d, 3: nn.BatchNorm3d}


class CPUOPsTester(TestCase):
    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_channelshuffle(self):
        channel_shuffle = torch.nn.ChannelShuffle(20)
        x = torch.randn(3, 40, 20, 20)
        x1 = x.clone()
        y1 = channel_shuffle(x1)

        # test channels last
        x2 = x.clone().to(memory_format=torch.channels_last)
        y2 = channel_shuffle(x2)
        self.assertTrue(y2.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(y1, y2)

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_pixel_shuffle_unshuffle(self):
        def _test_pixel_shuffle_unshuffle_helper(
            num_input_dims, valid_channels_dim=True, upscale_factor=None
        ):
            # Function to imperatively ensure pixels are shuffled to the correct locations.
            # Used to validate the batch operations in pixel_shuffle.
            def _verify_pixel_shuffle(input, output, upscale_factor):
                for c in range(output.size(-3)):
                    for h in range(output.size(-2)):
                        for w in range(output.size(-1)):
                            height_idx = h // upscale_factor
                            weight_idx = w // upscale_factor
                            channel_idx = (
                                (upscale_factor * (h % upscale_factor))
                                + (w % upscale_factor)
                                + (c * upscale_factor**2)
                            )
                            self.assertEqual(
                                output[..., c, h, w],
                                input[..., channel_idx, height_idx, weight_idx],
                            )

            upscale_factor = (
                random.randint(2, 5) if upscale_factor is None else upscale_factor
            )
            # If valid_channels_dim=False, add 1 to make channels dim indivisible by upscale_factor ** 2.
            channels = random.randint(1, 4) * upscale_factor**2 + (
                0 if valid_channels_dim else 1
            )
            height = random.randint(5, 10)
            width = random.randint(5, 10)
            if num_input_dims == 1:
                input = torch.rand(channels, requires_grad=True)
            elif num_input_dims == 2:
                input = torch.rand(height, width, requires_grad=True)
            else:
                batch_sizes = [random.randint(1, 3) for _ in range(num_input_dims - 3)]
                input = torch.rand(
                    *batch_sizes, channels, height, width, requires_grad=True
                )
            ps = nn.PixelShuffle(upscale_factor)
            pus = nn.PixelUnshuffle(downscale_factor=upscale_factor)
            if num_input_dims >= 3 and valid_channels_dim and upscale_factor > 0:
                output = ps(input)
                _verify_pixel_shuffle(input, output, upscale_factor)
                output.backward(output.data)
                self.assertEqual(input.data, input.grad.data)
                # Ensure unshuffle properly inverts shuffle.
                unshuffle_output = pus(output)
                self.assertEqual(input, unshuffle_output)
            else:
                self.assertRaises(RuntimeError, lambda: ps(input))

        def _test_pixel_unshuffle_error_case_helper(
            num_input_dims,
            valid_height_dim=True,
            valid_width_dim=True,
            downscale_factor=None,
        ):
            downscale_factor = (
                random.randint(2, 5) if downscale_factor is None else downscale_factor
            )
            channels = random.randint(1, 4)
            # If valid_height_dim=False, add 1 to make height dim indivisible by downscale_factor.
            height = random.randint(3, 5) * abs(downscale_factor) + (
                0 if valid_height_dim else 1
            )
            # If valid_width_dim=False, add 1 to make width dim indivisible by downscale_factor.
            width = random.randint(3, 5) * abs(downscale_factor) + (
                0 if valid_width_dim else 1
            )
            if num_input_dims == 1:
                input = torch.rand(channels, requires_grad=True)
            elif num_input_dims == 2:
                input = torch.rand(height, width, requires_grad=True)
            else:
                batch_sizes = [random.randint(1, 3) for _ in range(num_input_dims - 3)]
                input = torch.rand(
                    *batch_sizes, channels, height, width, requires_grad=True
                )
            pus = nn.PixelUnshuffle(downscale_factor)
            self.assertRaises(RuntimeError, lambda: pus(input))

        def _test_pixel_shuffle_unshuffle_for_input_dims(num_input_dims):
            # For 1D - 2D, this is an error case.
            # For 3D - 5D, this is a success case for pixel_shuffle + pixel_unshuffle.
            _test_pixel_shuffle_unshuffle_helper(num_input_dims=num_input_dims)
            # Error cases for pixel_shuffle.
            _test_pixel_shuffle_unshuffle_helper(
                num_input_dims=num_input_dims, valid_channels_dim=False
            )
            _test_pixel_shuffle_unshuffle_helper(
                num_input_dims=num_input_dims, upscale_factor=0
            )
            _test_pixel_shuffle_unshuffle_helper(
                num_input_dims=num_input_dims, upscale_factor=-2
            )
            # Error cases for pixel_unshuffle.
            _test_pixel_unshuffle_error_case_helper(
                num_input_dims=num_input_dims, valid_height_dim=False
            )
            _test_pixel_unshuffle_error_case_helper(
                num_input_dims=num_input_dims, valid_width_dim=False
            )
            _test_pixel_unshuffle_error_case_helper(
                num_input_dims=num_input_dims, downscale_factor=0
            )
            _test_pixel_unshuffle_error_case_helper(
                num_input_dims=num_input_dims, downscale_factor=-2
            )

        def test_pixel_shuffle_unshuffle_1D():
            _test_pixel_shuffle_unshuffle_for_input_dims(num_input_dims=1)

        def test_pixel_shuffle_unshuffle_2D():
            _test_pixel_shuffle_unshuffle_for_input_dims(num_input_dims=2)

        def test_pixel_shuffle_unshuffle_3D():
            _test_pixel_shuffle_unshuffle_for_input_dims(num_input_dims=3)

        def test_pixel_shuffle_unshuffle_4D():
            _test_pixel_shuffle_unshuffle_for_input_dims(num_input_dims=4)

        def test_pixel_shuffle_unshuffle_5D():
            _test_pixel_shuffle_unshuffle_for_input_dims(num_input_dims=5)

        test_pixel_shuffle_unshuffle_1D()
        test_pixel_shuffle_unshuffle_2D()
        test_pixel_shuffle_unshuffle_3D()
        test_pixel_shuffle_unshuffle_4D()
        test_pixel_shuffle_unshuffle_5D()

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_pixel_shuffle_nhwc_cpu(self):
        input = torch.randn(3, 18, 4, 4, device="cpu")
        input = input.contiguous(memory_format=torch.channels_last).requires_grad_()
        grad = torch.randn(3, 18, 4, 4, device="cpu")
        ps = torch.nn.PixelShuffle(3)
        pus = torch.nn.PixelUnshuffle(3)

        ref_input = input.detach().clone().contiguous().requires_grad_(True)
        ref_grad = grad.detach().clone().contiguous()
        ref_ps = torch.nn.PixelShuffle(3)
        ref_pus = torch.nn.PixelUnshuffle(3)

        out = pus(ps(input))
        out.backward(grad)
        ref_out = ref_pus(ref_ps(ref_input))
        ref_out.backward(ref_grad)

        self.assertTrue(out.is_contiguous(memory_format=torch.channels_last))
        self.assertTrue(ref_out.is_contiguous())
        self.assertEqual(out, ref_out)
        self.assertEqual(input.grad, ref_input.grad)

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_batch_norm(self):
        for dim in [2, 3]:
            m = bn_m[dim](10)
            input_size = [3, 10, 25, 25]
            if dim == 3:
                input_size.append(25)
            x = torch.randn(input_size)
            x1 = x.clone().detach().requires_grad_()
            y1 = m(x1)
            y1.mean().backward()

            # test channels last
            suggest_memory_format = (
                torch.channels_last if dim == 2 else torch.channels_last_3d
            )
            x2 = (
                x.clone()
                .detach()
                .to(memory_format=suggest_memory_format)
                .requires_grad_()
            )

            y2 = m(x2)
            y2.mean().backward()
            self.assertTrue(y2.is_contiguous(memory_format=suggest_memory_format))
            self.assertEqual(y1, y2)
            self.assertTrue(x2.grad.is_contiguous(memory_format=suggest_memory_format))
            self.assertEqual(x1.grad, x2.grad)

            # test bfloat16
            x3 = x.clone().detach().bfloat16().requires_grad_()
            y3 = m(x3)
            y3.mean().backward()
            self.assertTrue(y3.dtype == torch.bfloat16)
            self.assertEqual(y1, y3, prec=0.1)
            self.assertTrue(x3.grad.dtype == torch.bfloat16)
            self.assertEqual(x1.grad, x3.grad)

            # test autocast
            with torch.cpu.amp.autocast():
                for datatype in (torch.bfloat16, torch.float32):
                    x4 = x.clone().detach().to(datatype).requires_grad_()
                    y4 = m(x4)
                    y4.mean().backward()
                    self.assertTrue(y4.dtype == datatype)
                    self.assertTrue(x4.grad.dtype == datatype)

                    x5 = (
                        x.clone()
                        .detach()
                        .to(datatype)
                        .to(memory_format=suggest_memory_format)
                        .requires_grad_()
                    )
                    y5 = m(x5)
                    y5.mean().backward()
                    self.assertTrue(y5.dtype == datatype)
                    self.assertTrue(x5.grad.dtype == datatype)
                    self.assertTrue(
                        y5.is_contiguous(memory_format=suggest_memory_format)
                    )
                    self.assertTrue(
                        x5.grad.is_contiguous(memory_format=suggest_memory_format)
                    )

            # test non-contiguous inputs
            x6 = torch.transpose(x.clone().detach(), 2, 3).requires_grad_()
            x_ref = x6.clone().detach().contiguous().requires_grad_()
            y6 = m(x6)
            y6.mean().backward()
            y_ref = m(x_ref)
            y_ref.mean().backward()
            self.assertEqual(y6, y_ref)
            self.assertEqual(x6.grad, x_ref.grad)

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_adaptive_avg_pool2d(self):
        m = nn.AdaptiveAvgPool2d((5, 7))
        x = torch.randn(3, 64, 8, 9)
        x1 = x.clone().detach().requires_grad_()
        y1 = m(x1)
        y1.mean().backward()

        # test channels last
        x2 = x.clone().detach().to(memory_format=torch.channels_last).requires_grad_()
        y2 = m(x2)
        y2.mean().backward()
        self.assertTrue(y2.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(y1, y2)
        self.assertTrue(x2.grad.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(x1.grad, x2.grad)

        # test bfloat16
        x3 = x.clone().detach().bfloat16().requires_grad_()
        y3 = m(x3)
        y3.mean().backward()
        self.assertTrue(y3.dtype == torch.bfloat16)
        self.assertEqual(y1, y3, prec=0.01)
        self.assertTrue(x3.grad.dtype == torch.bfloat16)
        self.assertEqual(x1.grad, x3.grad)

        # test autocast
        with torch.cpu.amp.autocast():
            for datatype in (torch.bfloat16, torch.float32):
                x4 = x.clone().detach().to(datatype).requires_grad_()
                y4 = m(x4)
                y4.mean().backward()
                self.assertTrue(y4.dtype == datatype)
                self.assertTrue(x4.grad.dtype == datatype)

                x5 = (
                    x.clone()
                    .detach()
                    .to(datatype)
                    .to(memory_format=torch.channels_last)
                    .requires_grad_()
                )
                y5 = m(x5)
                y5.mean().backward()
                self.assertTrue(y5.dtype == datatype)
                self.assertTrue(x5.grad.dtype == datatype)
                self.assertTrue(y5.is_contiguous(memory_format=torch.channels_last))
                self.assertTrue(
                    x5.grad.is_contiguous(memory_format=torch.channels_last)
                )

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_copy(self):
        x = torch.randn(3, 64, 8, 9)
        y = torch.empty(3, 64, 8, 9)
        y.copy_(x)
        self.assertEqual(x, y)

        # test channels last
        y1 = torch.empty(3, 64, 8, 9).to(memory_format=torch.channels_last)
        y1.copy_(x)
        self.assertTrue(y1.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(x, y1)

        # test bfloat16
        y2 = torch.empty(3, 64, 8, 9).bfloat16()
        y2.copy_(x)
        self.assertTrue(y2.dtype == torch.bfloat16)
        self.assertEqual(x, y2, prec=0.01)

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_max_pool2d(self):
        m = nn.MaxPool2d((3, 2), stride=(2, 1))
        x = torch.randn(20, 16, 50, 32)
        x1 = x.clone().detach().requires_grad_()
        y1 = m(x1)
        y1.mean().backward()

        # test channels last
        x2 = x.clone().detach().to(memory_format=torch.channels_last).requires_grad_()
        y2 = m(x2)
        y2.mean().backward()
        self.assertTrue(y2.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(y1, y2)
        self.assertTrue(x2.grad.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(x1.grad, x2.grad)

        # test bfloat16
        x3 = x.clone().detach().bfloat16().requires_grad_()
        y3 = m(x3)
        y3.mean().backward()
        self.assertTrue(y3.dtype == torch.bfloat16)
        self.assertEqual(y1, y3, prec=0.02)
        self.assertTrue(x3.grad.dtype == torch.bfloat16)
        self.assertEqual(x1.grad, x3.grad, prec=1e-4)

        # test autocast
        with torch.cpu.amp.autocast():
            for datatype in (torch.bfloat16, torch.float32):
                x4 = x.clone().detach().to(datatype).requires_grad_()
                y4 = m(x4)
                y4.mean().backward()
                self.assertTrue(y4.dtype == datatype)
                self.assertTrue(x4.grad.dtype == datatype)

                x5 = (
                    x.clone()
                    .detach()
                    .to(datatype)
                    .to(memory_format=torch.channels_last)
                    .requires_grad_()
                )
                y5 = m(x5)
                y5.mean().backward()
                self.assertTrue(y5.dtype == datatype)
                self.assertTrue(x5.grad.dtype == datatype)
                self.assertTrue(y5.is_contiguous(memory_format=torch.channels_last))
                self.assertTrue(
                    x5.grad.is_contiguous(memory_format=torch.channels_last)
                )

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_upsample_nearest1d(self):
        x = torch.randn(2, 2, 4)
        x1 = x.clone().detach().requires_grad_()
        y1 = F.interpolate(x1, scale_factor=2, mode="nearest")
        y1.mean().backward()

        # test bfloat16
        x3 = x.clone().detach().bfloat16().requires_grad_()
        y3 = F.interpolate(x3, scale_factor=2, mode="nearest")
        y3.mean().backward()
        self.assertTrue(y3.dtype == torch.bfloat16)
        self.assertEqual(y1, y3, prec=0.01)
        self.assertTrue(x3.grad.dtype == torch.bfloat16)
        self.assertEqual(x1.grad, x3.grad)

        # test autocast
        with torch.cpu.amp.autocast():
            for datatype in (torch.bfloat16, torch.float32):
                x4 = x.clone().detach().to(datatype).requires_grad_()
                y4 = F.interpolate(x4, scale_factor=2, mode="nearest")
                y4.mean().backward()
                self.assertTrue(y4.dtype == datatype)
                self.assertTrue(x4.grad.dtype == datatype)

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_upsample_nearest2d(self):
        x = torch.randn(2, 2, 4, 4)
        x1 = x.clone().detach().requires_grad_()
        y1 = F.interpolate(x1, scale_factor=2, mode="nearest")
        y1.mean().backward()

        # test channels last
        x2 = x.clone().detach().to(memory_format=torch.channels_last).requires_grad_()
        y2 = F.interpolate(x2, scale_factor=2, mode="nearest")
        y2.mean().backward()
        self.assertTrue(y2.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(y1, y2)
        self.assertTrue(x2.grad.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(x1.grad, x2.grad)

        # test bfloat16
        x3 = x.clone().detach().bfloat16().requires_grad_()
        y3 = F.interpolate(x3, scale_factor=2, mode="nearest")
        y3.mean().backward()
        self.assertTrue(y3.dtype == torch.bfloat16)
        self.assertEqual(y1, y3, prec=0.01)
        self.assertTrue(x3.grad.dtype == torch.bfloat16)
        self.assertEqual(x1.grad, x3.grad)

        # test autocast
        with torch.cpu.amp.autocast():
            for datatype in (torch.bfloat16, torch.float32):
                x4 = x.clone().detach().to(datatype).requires_grad_()
                y4 = F.interpolate(x4, scale_factor=2, mode="nearest")
                y4.mean().backward()
                self.assertTrue(y4.dtype == datatype)
                self.assertTrue(x4.grad.dtype == datatype)

                x5 = (
                    x.clone()
                    .detach()
                    .to(datatype)
                    .to(memory_format=torch.channels_last)
                    .requires_grad_()
                )
                y5 = F.interpolate(x5, scale_factor=2, mode="nearest")
                y5.mean().backward()
                self.assertTrue(y5.dtype == datatype)
                self.assertTrue(x5.grad.dtype == datatype)
                self.assertTrue(y5.is_contiguous(memory_format=torch.channels_last))
                self.assertTrue(
                    x5.grad.is_contiguous(memory_format=torch.channels_last)
                )

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_upsample_nearest3d(self):
        x = torch.randn(2, 2, 2, 4, 4)
        x1 = x.clone().detach().requires_grad_()
        y1 = F.interpolate(x1, scale_factor=2, mode="nearest")
        y1.mean().backward()

        # test bfloat16
        x3 = x.clone().detach().bfloat16().requires_grad_()
        y3 = F.interpolate(x3, scale_factor=2, mode="nearest")
        y3.mean().backward()
        self.assertTrue(y3.dtype == torch.bfloat16)
        self.assertEqual(y1, y3, prec=0.01)
        self.assertTrue(x3.grad.dtype == torch.bfloat16)
        self.assertEqual(x1.grad, x3.grad)

        # test autocast
        with torch.cpu.amp.autocast():
            for datatype in (torch.bfloat16, torch.float32):
                x4 = x.clone().detach().to(datatype).requires_grad_()
                y4 = F.interpolate(x4, scale_factor=2, mode="nearest")
                y4.mean().backward()
                self.assertTrue(y4.dtype == datatype)
                self.assertTrue(x4.grad.dtype == datatype)

                x5 = (
                    x.clone()
                    .detach()
                    .to(datatype)
                    .to(memory_format=torch.channels_last_3d)
                    .requires_grad_()
                )
                y5 = F.interpolate(x5, scale_factor=2, mode="nearest")
                y5.mean().backward()
                self.assertTrue(y5.dtype == datatype)
                self.assertTrue(x5.grad.dtype == datatype)
                self.assertTrue(y5.is_contiguous(memory_format=torch.channels_last_3d))
                self.assertTrue(
                    x5.grad.is_contiguous(memory_format=torch.channels_last_3d)
                )

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_upsample_linear1d(self):
        x = torch.randn(2, 2, 4)
        x1 = x.clone().detach().requires_grad_()
        y1 = F.interpolate(x1, scale_factor=2, mode="linear")
        y1.mean().backward()

        # test bfloat16
        x3 = x.clone().detach().bfloat16().requires_grad_()
        y3 = F.interpolate(x3, scale_factor=2, mode="linear")
        y3.mean().backward()
        self.assertTrue(y3.dtype == torch.bfloat16)
        self.assertEqual(y1, y3, prec=0.01)
        self.assertTrue(x3.grad.dtype == torch.bfloat16)
        self.assertEqual(x1.grad, x3.grad)

        # test autocast
        with torch.cpu.amp.autocast():
            for datatype in (torch.bfloat16, torch.float32):
                x4 = x.clone().detach().to(datatype).requires_grad_()
                y4 = F.interpolate(x4, scale_factor=2, mode="linear")
                y4.mean().backward()
                self.assertTrue(y4.dtype == datatype)
                self.assertTrue(x4.grad.dtype == datatype)

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_upsample_bilinear2d(self):
        x = torch.randn(2, 2, 4, 4)
        x1 = x.clone().detach().requires_grad_()
        y1 = F.interpolate(x1, scale_factor=2, mode="bilinear")
        y1.mean().backward()

        # test channels last
        x2 = x.clone().detach().to(memory_format=torch.channels_last).requires_grad_()
        y2 = F.interpolate(x2, scale_factor=2, mode="bilinear")
        y2.mean().backward()
        self.assertTrue(y2.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(y1, y2)
        self.assertTrue(x2.grad.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(x1.grad, x2.grad)

        # test bfloat16
        x3 = x.clone().detach().bfloat16().requires_grad_()
        y3 = F.interpolate(x3, scale_factor=2, mode="bilinear")
        y3.mean().backward()
        self.assertTrue(y3.dtype == torch.bfloat16)
        self.assertEqual(y1, y3, prec=0.01)
        self.assertTrue(x3.grad.dtype == torch.bfloat16)
        self.assertEqual(x1.grad, x3.grad)

        # test autocast
        with torch.cpu.amp.autocast():
            for datatype in (torch.bfloat16, torch.float32):
                x4 = x.clone().detach().to(datatype).requires_grad_()
                y4 = F.interpolate(x4, scale_factor=2, mode="bilinear")
                y4.mean().backward()
                self.assertTrue(y4.dtype == datatype)
                self.assertTrue(x4.grad.dtype == datatype)

                x5 = (
                    x.clone()
                    .detach()
                    .to(datatype)
                    .to(memory_format=torch.channels_last)
                    .requires_grad_()
                )
                y5 = F.interpolate(x5, scale_factor=2, mode="bilinear")
                y5.mean().backward()
                self.assertTrue(y5.dtype == datatype)
                self.assertTrue(x5.grad.dtype == datatype)
                self.assertTrue(y5.is_contiguous(memory_format=torch.channels_last))
                self.assertTrue(
                    x5.grad.is_contiguous(memory_format=torch.channels_last)
                )

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_upsample_trilinear3d(self):
        x = torch.randn(2, 2, 2, 4, 4)
        x1 = x.clone().detach().requires_grad_()
        y1 = F.interpolate(x1, scale_factor=2, mode="trilinear")
        y1.mean().backward()

        # test bfloat16
        x3 = x.clone().detach().bfloat16().requires_grad_()
        y3 = F.interpolate(x3, scale_factor=2, mode="trilinear")
        y3.mean().backward()
        self.assertTrue(y3.dtype == torch.bfloat16)
        self.assertEqual(y1, y3, prec=0.02)
        self.assertTrue(x3.grad.dtype == torch.bfloat16)
        self.assertEqual(x1.grad, x3.grad)

        # test autocast
        with torch.cpu.amp.autocast():
            for datatype in (torch.bfloat16, torch.float32):
                x4 = x.clone().detach().to(datatype).requires_grad_()
                y4 = F.interpolate(x4, scale_factor=2, mode="trilinear")
                y4.mean().backward()
                self.assertTrue(y4.dtype == datatype)
                self.assertTrue(x4.grad.dtype == datatype)

                x5 = (
                    x.clone()
                    .detach()
                    .to(datatype)
                    .to(memory_format=torch.channels_last_3d)
                    .requires_grad_()
                )
                y5 = F.interpolate(x5, scale_factor=2, mode="trilinear")
                y5.mean().backward()
                self.assertTrue(y5.dtype == datatype)
                self.assertTrue(x5.grad.dtype == datatype)
                self.assertTrue(y5.is_contiguous(memory_format=torch.channels_last_3d))
                self.assertTrue(
                    x5.grad.is_contiguous(memory_format=torch.channels_last_3d)
                )

    def test_GroupNorm_memory_format(self):
        def helper(input_format, grad_format, B=2, C=4, W=4, H=4):
            net_orig = torch.nn.GroupNorm(B, C)
            net = copy.deepcopy(net_orig)
            x_orig = torch.rand(B, C, W, H, requires_grad=True)
            grad_orig = torch.rand(B, C, W, H)
            x = (
                x_orig.clone()
                .detach()
                .to(memory_format=input_format)
                .requires_grad_(True)
            )
            grad = grad_orig.detach().to(memory_format=grad_format)
            y = net(x)
            y.backward(grad)
            y_orig = net_orig(x_orig)
            y_orig.backward(grad_orig)

            self.assertEqual(y, y_orig)
            self.assertEqual(x.grad, x_orig.grad)

        for input_format in [torch.contiguous_format, torch.channels_last]:
            for grad_format in [torch.contiguous_format, torch.channels_last]:
                helper(input_format, grad_format)

    def test_groupNorm_mixed_dtype(self):
        def helper(size, groups, memory_format, dtype):
            channels = size[1]
            input = torch.randn(size).cpu().to(dtype=dtype)
            input_bf1 = (
                input.contiguous(memory_format=memory_format)
                .detach()
                .requires_grad_(True)
            )
            input_bf2 = input_bf1.clone().detach().requires_grad_(True)
            input_f = input_bf1.float().detach().requires_grad_(True)
            m_bf = nn.GroupNorm(groups, channels).cpu().to(dtype=dtype)
            m_f = deepcopy(m_bf).float()
            m_f2 = deepcopy(m_f)
            # bfloat16 input and bfloat16 parameters
            out = m_bf(input_bf1)
            # bfloat16 input and float parameters
            out2 = m_f(input_bf2)
            # float input and float parameters
            out3 = m_f2(input_f)
            torch.testing.assert_close(out, out2, atol=5e-3, rtol=5e-3)
            torch.testing.assert_close(out2.float(), out3, atol=5e-3, rtol=5e-3)
            grad_out = torch.randn(out2.shape).cpu().to(dtype=dtype)
            grad_out_bf1 = (
                grad_out.contiguous(memory_format=memory_format)
                .detach()
                .requires_grad_(True)
            )
            grad_out_bf2 = grad_out_bf1.clone().detach().requires_grad_(True)
            grad_out_f = grad_out_bf2.clone().float().detach().requires_grad_(True)
            # bfloat16 input grad and float parameters
            out2.backward(grad_out_bf2, retain_graph=True)
            # float input grad and float parameters
            out3.backward(grad_out_f, retain_graph=True)
            # bfloat16 input grad and bfloat16 parameters
            out.backward(grad_out_bf1, retain_graph=True)
            torch.testing.assert_close(
                m_f.weight.grad, m_f2.weight.grad, atol=2e-5, rtol=1e-5
            )
            torch.testing.assert_close(
                m_f.bias.grad, m_f2.bias.grad, atol=1e-5, rtol=1e-5
            )
            torch.testing.assert_close(
                input_bf2.grad.float(), input_f.grad, atol=5e-5, rtol=5e-3
            )
            # full bf16 has lower precision compared with mixed bf16 and fp32.
            atol = None
            rtol = None
            if dtype == torch.bfloat16:
                atol = 1e-2
                rtol = 1.3e-1
            else:
                assert dtype == torch.half
                atol = 5e-3
                rtol = 1.5e-2
            torch.testing.assert_close(
                m_bf.weight.grad.float(), m_f.weight.grad, atol=atol, rtol=rtol
            )
            torch.testing.assert_close(
                m_bf.bias.grad.float(), m_f.bias.grad, atol=atol, rtol=rtol
            )
            torch.testing.assert_close(
                input_bf1.grad, input_bf2.grad, atol=atol, rtol=rtol
            )

        cl_formats = {4: torch.channels_last, 5: torch.channels_last_3d}
        for dtype in [torch.bfloat16, torch.half]:
            for shape, g in [
                ((1, 8, 4, 3), 2),
                ((1, 8, 3, 4), 4),
                ((4, 40, 40, 40), 2),
                ((4, 8, 40, 40), 4),
                ((1, 8, 40, 40), 4),
                ((1, 8, 40, 40), 2),
                ((1, 8, 50, 50), 2),
                ((1, 8, 50, 50), 4),
                ((1, 40, 50, 50), 2),
                ((1, 9, 3, 4, 5), 3),
                ((1, 60, 10, 10, 10), 3),
                ((1, 9, 10, 50, 50), 3),
                ((1, 60, 10, 50, 50), 3),
                ((1, 8, 65, 55), 2),
                ((1, 3, 65, 55), 1),
                ((1, 3, 20, 20), 1),
            ]:
                for is_cl in [False, True]:
                    format = (
                        cl_formats[len(shape)] if is_cl else torch.contiguous_format
                    )
                    helper(shape, g, format, dtype)

    def test_groupnorm_nhwc(self):
        def helper(self, size, groups, memory_format, dtype, is_mixed):
            channels = size[1]
            input = torch.randn(size, dtype=dtype, requires_grad=True)
            input = input.contiguous(memory_format=memory_format)
            input.retain_grad()
            grad = torch.randn(size, dtype=dtype)
            grad = grad.contiguous(memory_format=memory_format)
            if dtype == torch.bfloat16 and is_mixed:
                gn = nn.GroupNorm(groups, channels).to(torch.float)
            else:
                gn = nn.GroupNorm(groups, channels).to(dtype)
            gn.weight.data.uniform_()
            gn.bias.data.uniform_()

            ref_input = (
                input.detach()
                .clone()
                .contiguous(memory_format=torch.contiguous_format)
                .requires_grad_(True)
            )
            ref_grad = (
                grad.detach().clone().contiguous(memory_format=torch.contiguous_format)
            )
            if dtype == torch.bfloat16 and is_mixed:
                ref_gn = nn.GroupNorm(groups, channels).to(torch.float)
            else:
                ref_gn = nn.GroupNorm(groups, channels).to(dtype)
            ref_gn.load_state_dict(gn.state_dict())

            out = gn(input)
            out.backward(grad)
            ref_out = ref_gn(ref_input)
            ref_out.backward(ref_grad)

            self.assertTrue(out.is_contiguous(memory_format=memory_format))
            self.assertTrue(
                ref_out.is_contiguous(memory_format=torch.contiguous_format)
            )
            torch.testing.assert_close(out, ref_out)
            # parameters in bfloat16/Half is not recommended
            atol = 5e-4
            rtol = 8e-3
            torch.testing.assert_close(
                gn.weight.grad, ref_gn.weight.grad, atol=atol, rtol=rtol
            )
            torch.testing.assert_close(
                gn.bias.grad, ref_gn.bias.grad, atol=atol, rtol=rtol
            )
            torch.testing.assert_close(input.grad, ref_input.grad, atol=atol, rtol=rtol)

        for dtype in [torch.float16, torch.bfloat16, torch.float, torch.double]:
            for is_mixed in [True, False]:
                helper(self, (4, 8, 10, 10), 4, torch.channels_last, dtype, is_mixed)
                helper(self, (2, 30, 9, 9), 3, torch.channels_last, dtype, is_mixed)
                helper(self, (4, 8, 40, 40), 4, torch.channels_last, dtype, is_mixed)
                helper(self, (4, 40, 40, 40), 2, torch.channels_last, dtype, is_mixed)
                helper(self, (2, 30, 50, 50), 3, torch.channels_last, dtype, is_mixed)
                helper(self, (2, 60, 50, 50), 3, torch.channels_last, dtype, is_mixed)
                helper(
                    self, (2, 9, 7, 11, 15), 3, torch.channels_last_3d, dtype, is_mixed
                )
                helper(
                    self, (2, 9, 7, 200, 15), 3, torch.channels_last_3d, dtype, is_mixed
                )
                helper(
                    self,
                    (2, 60, 7, 200, 15),
                    3,
                    torch.channels_last_3d,
                    dtype,
                    is_mixed,
                )

    def test_groupnorm_nwc(self):
        size = (4, 20, 20)
        channels = size[1]
        groups = 4
        x = torch.randn(size, requires_grad=True)
        grad = torch.randn(size)
        m = nn.GroupNorm(groups, channels)

        # test nwc
        x1 = x.clone().detach().transpose(1, 2).requires_grad_()
        grad1 = grad.detach().clone()
        y1 = m(x1)
        y1.backward(grad1)

        x2 = x1.clone().detach().contiguous().requires_grad_()
        grad2 = grad.detach().clone()
        y2 = m(x2)
        y2.backward(grad2)
        self.assertEqual(y1, y2)
        self.assertEqual(x1.grad, x2.grad)

        # test bfloat16/double
        for dtype in [torch.bfloat16, torch.double]:
            prec = None
            if dtype == torch.bfloat16:
                prec = 0.03
            x3 = x.clone().detach().transpose(1, 2).to(dtype).requires_grad_()
            grad3 = grad.detach().clone()
            m_dtype = m.to(dtype)
            y3 = m_dtype(x3)
            y3.backward(grad3)
            self.assertTrue(y3.dtype == dtype)
            self.assertEqual(y3, y2, prec=prec)
            self.assertEqual(x3.grad, x2.grad, prec=prec)
            self.assertEqual(m.weight.grad, m_dtype.weight.grad)
            self.assertEqual(m.bias.grad, m_dtype.bias.grad)

        # test mixed data type
        prec = 0.02
        x_bf16 = x.clone().detach().transpose(1, 2).to(torch.bfloat16).requires_grad_()
        grad_bf16 = grad.clone().detach().to(torch.bfloat16)
        m_fp32 = copy.deepcopy(m).to(torch.float32)
        y_bf16 = m_fp32(x_bf16)
        y_bf16.backward(grad_bf16)
        self.assertTrue(y_bf16.dtype == torch.bfloat16)
        self.assertEqual(y_bf16, y2, prec=prec)
        self.assertTrue(x_bf16.grad.dtype == torch.bfloat16)
        self.assertEqual(x_bf16.grad, x2.grad, prec=prec)

    def test_avg_pool2d(self):
        def helper(self, m, x):
            x1 = x.clone().detach().requires_grad_()
            y1 = m(x1)
            y1.backward(y1.data)

            # test channels last
            x2 = (
                x.clone()
                .detach()
                .to(memory_format=torch.channels_last)
                .requires_grad_()
            )
            y2 = m(x2)
            y2.backward(y2.data)
            self.assertTrue(y2.is_contiguous(memory_format=torch.channels_last))
            self.assertEqual(y1, y2)
            self.assertTrue(x2.grad.is_contiguous(memory_format=torch.channels_last))
            self.assertEqual(x1.grad, x2.grad)

            for dtype in [torch.bfloat16, torch.double, torch.int64, torch.float16]:
                x3 = x.clone().detach().to(dtype)
                x4 = x.clone().detach().to(dtype).to(memory_format=torch.channels_last)
                if dtype != torch.int64:
                    x3 = x3.requires_grad_()
                    x4 = x4.requires_grad_()
                y3 = m(x3)
                y4 = m(x4)
                self.assertTrue(y3.dtype == dtype)
                self.assertTrue(y4.dtype == dtype)
                self.assertEqual(y3, y4)
                self.assertTrue(y4.is_contiguous(memory_format=torch.channels_last))
                if dtype != torch.int64:
                    y3.backward(y3.data)
                    self.assertTrue(x3.grad.dtype == dtype)
                    if dtype == torch.bfloat16:
                        self.assertEqual(y1, y3, prec=0.01)
                        self.assertEqual(x1.grad, x3.grad, prec=0.01)
                if dtype != torch.int64:
                    y4.backward(y4.data)
                    self.assertEqual(x3.grad, x4.grad)
                    self.assertTrue(x4.grad.dtype == dtype)
                    self.assertTrue(
                        x4.grad.is_contiguous(memory_format=torch.channels_last)
                    )

        helper(self, nn.AvgPool2d((3, 2), stride=(2, 1)), torch.randn(20, 16, 50, 32))
        helper(self, nn.AvgPool2d((3, 2), stride=(2, 1)), torch.randn(10, 8, 25, 16))
        helper(
            self,
            nn.AvgPool2d((3, 2), stride=(2, 1), count_include_pad=False),
            torch.randn(20, 16, 50, 32),
        )
        helper(
            self,
            nn.AvgPool2d(
                (3, 2), stride=(2, 1), count_include_pad=True, divisor_override=100
            ),
            torch.randn(20, 16, 50, 32),
        )
        helper(
            self,
            nn.AvgPool2d(
                (3, 2), stride=(2, 1), count_include_pad=True, divisor_override=100
            ),
            torch.randn(10, 8, 25, 16),
        )

    # Keep this UT temporarily to make sure the OP behavior in PyTorch is as expected.
    def test_adaptive_max_pool2d(self):
        m = nn.AdaptiveMaxPool2d((5, 7))
        x = torch.randn(3, 64, 8, 9)
        x1 = x.clone().detach().requires_grad_()
        y1 = m(x1)
        y1.mean().backward()

        # test channels last
        x2 = x.clone().detach().to(memory_format=torch.channels_last).requires_grad_()
        y2 = m(x2)
        y2.mean().backward()
        self.assertTrue(y2.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(y1, y2)
        self.assertTrue(x2.grad.is_contiguous(memory_format=torch.channels_last))
        self.assertEqual(x1.grad, x2.grad)

        # test bfloat16
        x3 = x.clone().detach().bfloat16().requires_grad_()
        y3 = m(x3)
        y3.mean().backward()
        self.assertTrue(y3.dtype == torch.bfloat16)
        self.assertEqual(y1, y3, prec=0.01)
        self.assertTrue(x3.grad.dtype == torch.bfloat16)
        self.assertEqual(x1.grad, x3.grad, prec=0.001)

        # test autocast
        with torch.cpu.amp.autocast():
            for datatype in (torch.bfloat16, torch.float32):
                x4 = x.clone().detach().to(datatype).requires_grad_()
                y4 = m(x4)
                y4.mean().backward()
                self.assertTrue(y4.dtype == datatype)
                self.assertTrue(x4.grad.dtype == datatype)

                x5 = (
                    x.clone()
                    .detach()
                    .to(datatype)
                    .to(memory_format=torch.channels_last)
                    .requires_grad_()
                )
                y5 = m(x5)
                y5.mean().backward()
                self.assertTrue(y5.dtype == datatype)
                self.assertTrue(x5.grad.dtype == datatype)
                self.assertTrue(y5.is_contiguous(memory_format=torch.channels_last))
                self.assertTrue(
                    x5.grad.is_contiguous(memory_format=torch.channels_last)
                )

    def test_avg_pool3d_ndhwc(self):
        def helper(
            n,
            c,
            d,
            h,
            w,
            kernel_size,
            dtype,
            contig,
            count_include_pad=True,
            divisor_override=None,
        ):
            input = torch.randint(1, 10, (n, c, d, h, w), device="cpu", dtype=dtype)
            input = input.contiguous(memory_format=torch.channels_last_3d)
            if not contig:
                input = input[:, ::2, :, :, :]
            pool = torch.nn.AvgPool3d(
                kernel_size=kernel_size,
                count_include_pad=count_include_pad,
                divisor_override=divisor_override,
            )
            ref_input = input.detach().clone().contiguous()
            if dtype != torch.int64:
                input = input.requires_grad_()
                ref_input = ref_input.requires_grad_()

            out = pool(input)
            ref_out = pool(ref_input)

            self.assertTrue(out.is_contiguous(memory_format=torch.channels_last_3d))
            self.assertTrue(ref_out.is_contiguous())

            if dtype != torch.int64:
                out.backward(out.data)
                ref_out.backward(ref_out.data)
                self.assertEqual(out, ref_out)
                self.assertEqual(input.grad, ref_input.grad)

        for dtype in [torch.int64, torch.float32, torch.double]:
            for contig in [True, False]:
                for count_include_pad in [True, False]:
                    helper(
                        4,
                        8,
                        10,
                        10,
                        10,
                        (3, 2, 3),
                        dtype,
                        contig,
                        count_include_pad=count_include_pad,
                    )
                    helper(
                        4,
                        8,
                        18,
                        9,
                        14,
                        (2, 3, 2),
                        dtype,
                        contig,
                        count_include_pad=count_include_pad,
                    )
                    helper(
                        4,
                        8,
                        7,
                        8,
                        9,
                        (2, 2, 2),
                        dtype,
                        contig,
                        count_include_pad=count_include_pad,
                        divisor_override=100,
                    )

    def test_avg_pool(self):
        def helper(input, kernel_size):
            if input.ndim == 4:
                pool = torch.nn.AvgPool3d(kernel_size=kernel_size)
                input = input.contiguous(
                    memory_format=torch.channels_last
                ).requires_grad_()
                self.assertRaises(RuntimeError, lambda: pool(input))
                ref_input = input.detach().clone().contiguous().requires_grad_(True)
                ref_out = pool(ref_input)
                ref_out.backward(ref_out.data)
            elif input.ndim == 3:
                pool = torch.nn.AvgPool2d(kernel_size=kernel_size)
                input = input.requires_grad_()
                out = pool(input)
                input2 = input.detach().clone().to(torch.bfloat16).requires_grad_()
                out2 = pool(input2)
                out.backward(out.data)
                out2.backward(out2.data)
                self.assertEqual(out, out2, 0.01)
                self.assertEqual(input.grad, input2.grad, 0.01)

        helper(torch.rand(4, 8, 10, 10), (3, 2, 3))
        helper(torch.rand(4, 8, 10), (3, 2))

    @skipIfNoTorchVision
    def test_torchvision_nms(self):
        num_boxes = 50
        boxes = torch.randn(num_boxes, 4)
        boxes[:, 2:] += boxes[:, :2]
        scores = torch.randn(num_boxes)
        y1 = torchvision.ops.nms(boxes, scores, 0.5)

        # test autocast
        with torch.cpu.amp.autocast():
            y2 = torchvision.ops.nms(boxes.bfloat16(), scores.bfloat16(), 0.5)
            self.assertEqual(y1, y2)

        # test double
        y3 = torchvision.ops.nms(boxes.double(), scores.double(), 0.5)
        self.assertEqual(y1, y3)

    def test_mean(self):
        x = torch.randn(1, 64, 100, 13, 24, requires_grad=True)
        for dtype in [torch.float32, torch.double, torch.bfloat16]:
            y1 = torch.mean(x, dim=(3, 4), keepdim=False, dtype=dtype)
            x2 = (
                x.clone()
                .detach()
                .to(memory_format=torch.channels_last_3d)
                .requires_grad_()
            )
            y2 = torch.mean(x2, dim=(3, 4), keepdim=False, dtype=dtype)
            self.assertEqual(y1, y2)

    def test_sum(self):
        def helper(self, x1, x2, dim, keepdim, dtype):
            y1 = torch.sum(x1, dim=dim, keepdim=keepdim, dtype=dtype)
            y2 = torch.sum(x2, dim=dim, keepdim=keepdim, dtype=dtype)
            self.assertEqual(y1, y2, prec=2e-4)

        dtypes = [
            torch.float32,
            torch.double,
            torch.bfloat16,
            torch.float16,
            torch.complex64,
            torch.complex128,
        ]
        x1 = torch.randn((1, 128, 56, 56)).to(memory_format=torch.channels_last)
        x1 = x1.reshape([1, 2, 64, 56, 56])
        x2 = x1.detach().clone().contiguous()
        x3 = torch.randn((1, 64, 100, 13, 24)).to(memory_format=torch.channels_last_3d)
        x4 = x3.detach().clone().contiguous()
        x5 = torch.randn((1, 10, 16, 16)).to(memory_format=torch.channels_last)
        x6 = x5.detach().clone().contiguous()
        x7 = torch.randn((1, 1, 1, 1)).to(memory_format=torch.channels_last)
        x8 = x7.detach().clone().contiguous()
        x9 = torch.randn((1, 10, 256, 256)).to(memory_format=torch.channels_last)
        x10 = x9.detach().clone().contiguous()
        x11 = (
            torch.randn((224, 1, 224))
            .unsqueeze(0)
            .to(memory_format=torch.channels_last)
            .squeeze(0)
        )
        x12 = x11.detach().clone().contiguous()
        x13 = (
            torch.randn((3, 1, 224))
            .unsqueeze(0)
            .to(memory_format=torch.channels_last)
            .squeeze(0)
        )
        x14 = x13.detach().clone().contiguous()
        for dtype in dtypes:
            for dim in [(1), (-1, -2)]:
                for keepdim in [True, False]:
                    helper(self, x1, x2, dim, keepdim, dtype)
                    helper(self, x3, x4, dim, keepdim, dtype)
                    helper(self, x5, x6, dim, keepdim, dtype)
                    helper(self, x7, x8, dim, keepdim, dtype)
                    helper(self, x9, x10, dim, keepdim, dtype)
                    helper(self, x11, x12, dim, keepdim, dtype)
                    helper(self, x13, x14, dim, keepdim, dtype)

        a = torch.randn([3, 2, 3])
        mask = a.ge(0.5)
        s = mask.sum()
        self.assertTrue(s.dtype != torch.bool)

        # add ut for special case - not a true reduction in sumkernel
        for dtype in [torch.float32, torch.bfloat16, torch.double]:
            x5 = torch.rand(789, 357).to(dtype)
            x6 = x5.detach().clone().transpose(0, 1)
            y5 = torch.mvlgamma(x5, p=1)
            y6 = torch.mvlgamma(x6, p=1).transpose(0, 1)
            self.assertEqual(y5, y6)

        x5 = torch.rand(789, 357).to(torch.float16)
        x6 = x5.detach().clone().transpose(0, 1)
        y5 = torch.arange(0, 0.5, 0.5).to(torch.float16).add(x5.unsqueeze(-1)).sum(-1)
        y6 = (
            torch.arange(0, 0.5, 0.5)
            .to(torch.float16)
            .add(x6.unsqueeze(-1))
            .sum(-1)
            .transpose(0, 1)
        )
        self.assertEqual(y5, y6)

    def test_matmul(self):
        def helper(a, b, c, op):
            dtypes = [torch.float32, torch.bfloat16]
            for dtype in dtypes:
                a = a.to(dtype)
                b = b.to(dtype)
                c = c.to(dtype)
                op(a, b, out=c)
                d = op(a, b)
                self.assertTrue(torch.equal(c, d))
                ipex.set_fp32_math_mode(mode=ipex.FP32MathMode.BF32, device="cpu")
                op(a, b, out=c)
                d = op(a, b)
                self.assertTrue(torch.equal(c, d))
                e = a.clone().requires_grad_()
                f = b.clone().requires_grad_()
                g = op(e, f)
                g.backward(g.data)
                h = op(a, f)
                h.backward(h.data)
                ipex.set_fp32_math_mode(mode=ipex.FP32MathMode.FP32, device="cpu")

        helper(torch.randn(2, 3), torch.randn(3, 4), torch.zeros(2, 4), torch.mm)
        helper(torch.randn(2, 3), torch.randn(3, 4), torch.zeros(2, 4), torch.matmul)
        helper(
            torch.randn(10, 3, 4),
            torch.randn(10, 4, 5),
            torch.zeros(10, 3, 5),
            torch.bmm,
        )
        helper(
            torch.randn(10, 3, 4, 5),
            torch.randn(10, 3, 5, 5),
            torch.zeros(10, 3, 4, 5),
            torch.matmul,
        )
        helper(torch.randn(1), torch.randn(1), torch.zeros(1), torch.matmul)
        helper(torch.randn(2, 3), torch.randn(3), torch.zeros(2, 3), torch.matmul)
        helper(torch.randn(2, 3, 4), torch.randn(4), torch.zeros(2, 3, 4), torch.matmul)
        helper(torch.randn(3), torch.randn(3, 1), torch.zeros(3), torch.matmul)
        helper(
            torch.randn(2, 3), torch.randn(1, 3, 3), torch.zeros(1, 2, 3), torch.matmul
        )
        helper(torch.randn(3), torch.randn(1, 3, 3), torch.zeros(1, 3), torch.matmul)

        def f(x, y, z):
            return ((x.relu() * x) @ y.sin() @ z).sum()

        x = torch.randn(2, 3)
        y = torch.randn(3, 5)
        z = torch.randn(5, 5)
        ipex.set_fp32_math_mode(mode=ipex.FP32MathMode.BF32, device="cpu")
        result_forward_mode = autogradF.hessian(
            f, (x, y, z), outer_jacobian_strategy="forward-mode", vectorize=True
        )
        ipex.set_fp32_math_mode(mode=ipex.FP32MathMode.FP32, device="cpu")

    def test_index_select(self):
        for index_datatype in [torch.int32, torch.int64]:
            indices = torch.tensor([1], dtype=index_datatype)

            # test floating types
            for datatype in [
                torch.float32,
                torch.bfloat16,
                torch.double,
                torch.float16,
                torch.complex64,
                torch.complex128,
            ]:
                for dim in [0, 1]:
                    x1_1 = torch.randn((10, 2), dtype=datatype)
                    y1_1 = x1_1.index_select(dim, indices)
                    self.assertTrue(y1_1.dtype == datatype)

                    x1_2 = torch.randn((10, 10), dtype=datatype)
                    y1_2 = x1_2.index_select(dim, indices)
                    self.assertTrue(y1_2.dtype == datatype)

                    x1_3 = torch.randn((10, 40000), dtype=datatype)
                    y1_3 = x1_3.index_select(dim, indices)
                    self.assertTrue(y1_3.dtype == datatype)

                    x1_4 = torch.randn((40000, 5), dtype=datatype)
                    y1_4 = x1_4.index_select(dim, indices)
                    self.assertTrue(y1_4.dtype == datatype)

                for dim in [0, 1, 2]:
                    x1_5 = torch.randn((10, 2, 3), dtype=datatype)
                    y1_5 = x1_5.index_select(dim, indices)
                    self.assertTrue(y1_5.dtype == datatype)

                x1_6 = torch.randn((10), dtype=datatype)
                y1_6 = x1_6.index_select(0, indices)
                self.assertTrue(y1_6.dtype == datatype)

            # test integer types
            for datatype in [
                torch.int32,
                torch.int64,
                torch.int16,
                torch.int8,
                torch.uint8,
            ]:
                for dim in [0, 1]:
                    x2_1 = torch.randint(10, (10, 10), dtype=datatype)
                    y2_1 = x2_1.index_select(dim, indices)
                    self.assertTrue(y2_1.dtype == datatype)

                    x2_2 = torch.randint(10, (40000, 5), dtype=datatype)
                    y2_2 = x2_2.index_select(dim, indices)
                    self.assertTrue(y2_2.dtype == datatype)

                x2_3 = torch.randint(10, (10,), dtype=datatype)
                y2_3 = x2_3.index_select(0, indices)
                self.assertTrue(y2_3.dtype == datatype)

            # test bool
            for dim in [0, 1]:
                x3_1 = torch.randint(1, (10, 10), dtype=torch.bool)
                y3_1 = x3_1.index_select(dim, indices)
                self.assertTrue(y3_1.dtype == torch.bool)

                x3_2 = torch.randint(1, (40000, 5), dtype=torch.bool)
                y3_2 = x3_2.index_select(dim, indices)
                self.assertTrue(y3_2.dtype == torch.bool)

            x3_3 = torch.randint(1, (10,), dtype=torch.bool)
            y3_3 = x3_3.index_select(0, indices)
            self.assertTrue(y3_3.dtype == torch.bool)

            # out is defined
            for dim in [0, 1]:
                x1_5 = torch.randn(10, 2)
                y1_5 = torch.index_select(x1_5, dim, indices, out=torch.empty(0))
                self.assertTrue(y1_5.dtype == torch.float32)

    def test_cat(self):
        for datatype in [torch.float32, torch.double, torch.bfloat16, torch.float16]:
            for dim, size in itertools.product([0, 1], [[2, 1], [2, 2], [5, 10]]):
                x = torch.randn(size, dtype=datatype)
                y = torch.cat([x, x], dim)
                self.assertTrue(y.dtype == datatype)

            # long input tensor list
            x1 = torch.randn((2, 2), dtype=datatype)
            input1 = []
            for i in range(100):
                input1.append(x1)
            y1 = torch.cat(input1, 0)
            self.assertTrue(y1.size() == torch.Size([200, 2]))
            self.assertTrue(y1.dtype == datatype)

            # input tensors have different shapes and strides
            x2 = torch.randn((400, 2), dtype=datatype)
            input2 = []
            for i in range(10):
                input2.append(x1)
            for i in range(100):
                input2.append(x2)
            y2 = torch.cat(input2, 0)
            self.assertTrue(y2.size() == torch.Size([40020, 2]))
            self.assertTrue(y2.dtype == datatype)

            x3 = torch.randn((4000, 2), dtype=datatype)
            input3 = []
            for i in range(10):
                input3.append(x1)
            for i in range(10):
                input3.append(x3)
            y3 = torch.cat(input3, 0)
            self.assertTrue(y3.size() == torch.Size([40020, 2]))
            self.assertTrue(y3.dtype == datatype)

            x4 = torch.randn((4, 2), dtype=datatype)
            input4 = []
            for i in range(10):
                input4.append(x1)
            for i in range(10):
                input4.append(x4)
            y4 = torch.cat(input4, 0)
            self.assertTrue(y4.size() == torch.Size([60, 2]))
            self.assertTrue(y4.dtype == datatype)

            # "out" arg is used but  un-defined
            y5 = torch.cat([x4, x4], 0, out=torch.empty(0, dtype=datatype))
            self.assertEqual(y5, torch.cat([x4, x4], 0))
            self.assertTrue(y5.dtype == datatype)

            # out is defined with wrong shape
            ref = torch.cat([x4, x4], 0)
            out = torch.zeros(1)
            out_ptr = out.data_ptr()
            torch.cat([x4, x4], 0, out=out)
            self.assertEqual(ref, out)
            self.assertTrue(ref.dtype == datatype)
            self.assertTrue(out_ptr != out.data_ptr())

            # out is defined with correct shape
            ref = torch.cat([x4, x4], 0)
            out = torch.zeros_like(ref)
            out_ptr = out.data_ptr()
            torch.cat([x4, x4], 0, out=out)
            self.assertEqual(ref, out)
            self.assertTrue(ref.dtype == datatype)
            self.assertTrue(out_ptr == out.data_ptr())

            y6 = torch.cat([x4, x4], 0, out=torch.empty(0, dtype=torch.float32))
            self.assertEqual(y6, torch.cat([x4, x4], 0))
            self.assertTrue(y6.dtype == torch.float32)

            # one of input tensors is empty
            x7 = torch.empty(0, dtype=datatype)
            y7 = torch.cat([x4, x4, x7], 0)
            self.assertTrue(y7.size() == torch.Size([8, 2]))
            self.assertTrue(y7.dtype == datatype)

    def test_flash_attention_without_mask(self):
        dtypes = [torch.float, torch.double, torch.bfloat16, torch.float16]
        for dtype in dtypes:
            for causal in [True, False]:
                for batch_size, seq_len, n_head, head_dim in itertools.product(
                    [2, 12], [1, 129, 267, 533, 1030], [1, 3, 4], [7, 8, 16]
                ):
                    atol = 1e-5
                    rtol = 5e-6
                    if dtype is torch.bfloat16:
                        atol = 2e-2
                        rtol = 2e-2
                    if dtype is torch.float16:
                        atol = 1e-2
                        rtol = 1e-2

                    n_embd = n_head * head_dim
                    x = torch.randn(
                        (batch_size, seq_len, 3 * n_head * head_dim),
                        device="cpu",
                        dtype=dtype,
                        requires_grad=False,
                    )
                    x2 = x.clone()

                    q, k, v = x.split(n_embd, dim=2)
                    q2, k2, v2 = x2.split(n_embd, dim=2)

                    if dtype in [torch.bfloat16, torch.float16]:
                        q2 = q2.float()
                        k2 = k2.float()
                        v2 = v2.float()

                    # (B, nh, T, hs)
                    k = k.view(batch_size, seq_len, n_head, head_dim).transpose(1, 2)
                    q = q.view(batch_size, seq_len, n_head, head_dim).transpose(1, 2)
                    v = v.view(batch_size, seq_len, n_head, head_dim).transpose(1, 2)
                    k2 = k2.view(batch_size, seq_len, n_head, head_dim).transpose(1, 2)
                    q2 = q2.view(batch_size, seq_len, n_head, head_dim).transpose(1, 2)
                    v2 = v2.view(batch_size, seq_len, n_head, head_dim).transpose(1, 2)

                    actual = torch.ops.torch_ipex.flash_attention(
                        q,
                        k,
                        v,
                        dropout_p=0.0,
                        is_causal=causal,
                    )[0]
                    math_ref = (
                        torch._scaled_dot_product_attention_math(
                            q2,
                            k2,
                            v2,
                            dropout_p=0.0,
                            is_causal=causal,
                        )
                    )[0]

                    if dtype in [torch.bfloat16, torch.float16]:
                        math_ref = math_ref.to(dtype)
                    torch.testing.assert_close(actual, math_ref, atol=atol, rtol=rtol)

    def test_flash_attention_with_mask(self):
        import math

        dtypes = [torch.float, torch.double, torch.bfloat16, torch.float16]
        for dtype in dtypes:
            for is_causal in [True, False]:
                for mask_dim in [2, 4]:
                    for batch_size, seq_len, n_head, head_dim in itertools.product(
                        [2, 12], [1, 129, 267, 533, 1030], [1, 3, 4], [7, 8, 16]
                    ):
                        atol = 1e-5
                        rtol = 5e-6
                        if dtype is torch.bfloat16:
                            atol = 2e-2
                            rtol = 2e-2
                        if dtype is torch.float16:
                            atol = 1e-2
                            rtol = 1e-2
                        attn_mask_dtypes = (
                            [dtype, torch.bool, torch.float]
                            if dtype in [torch.bfloat16, torch.float16]
                            else [dtype, torch.bool]
                        )
                        for attn_mask_dtype in attn_mask_dtypes:
                            for attn_mask_shape in (
                                itertools.product([seq_len, 1], [seq_len, 1])
                                if mask_dim == 2
                                else itertools.product(
                                    [batch_size, 1],
                                    [n_head, 1],
                                    [seq_len, 1],
                                    [seq_len, 1],
                                )
                            ):
                                n_embd = n_head * head_dim
                                x = torch.randn(
                                    (batch_size, seq_len, 3 * n_head * head_dim),
                                    device="cpu",
                                    dtype=dtype,
                                    requires_grad=False,
                                )
                                x2 = x.clone()

                                q, k, v = x.split(n_embd, dim=2)
                                q2, k2, v2 = x2.split(n_embd, dim=2)

                                if dtype in [torch.bfloat16, torch.float16]:
                                    q2 = q2.float()
                                    k2 = k2.float()
                                    v2 = v2.float()

                                # (B, nh, T, hs)
                                k = k.view(
                                    batch_size, seq_len, n_head, head_dim
                                ).transpose(1, 2)
                                q = q.view(
                                    batch_size, seq_len, n_head, head_dim
                                ).transpose(1, 2)
                                v = v.view(
                                    batch_size, seq_len, n_head, head_dim
                                ).transpose(1, 2)
                                k2 = k2.view(
                                    batch_size, seq_len, n_head, head_dim
                                ).transpose(1, 2)
                                q2 = q2.view(
                                    batch_size, seq_len, n_head, head_dim
                                ).transpose(1, 2)
                                v2 = v2.view(
                                    batch_size, seq_len, n_head, head_dim
                                ).transpose(1, 2)

                                if attn_mask_dtype == torch.bool:
                                    mask = torch.ones(
                                        attn_mask_shape,
                                        dtype=torch.bool,
                                        device="cpu",
                                        requires_grad=False,
                                    ).tril(diagonal=0)
                                    # _scaled_dot_product_attention_math does the type conversion outside
                                    mask2 = torch.zeros_like(mask, dtype=dtype)
                                    mask2[mask == False] = -float("inf")  # noqa: E712
                                else:
                                    mask = torch.randn(
                                        attn_mask_shape,
                                        dtype=attn_mask_dtype,
                                        device="cpu",
                                        requires_grad=False,
                                    )
                                    mask2 = mask
                                actual = torch.ops.torch_ipex.flash_attention(
                                    q,
                                    k,
                                    v,
                                    dropout_p=0.0,
                                    attention_mask=mask,
                                    is_causal=is_causal,
                                )[0]
                                # math ref path with both is_causal and attn mask
                                attn_mask_shape = list(attn_mask_shape)
                                attn_mask_shape[-2] = q2.size(-2)
                                attn_mask_shape[-1] = k2.size(-2)
                                scale_factor = 1 / math.sqrt(q2.size(-1))
                                attn_bias = torch.zeros(attn_mask_shape, dtype=q2.dtype)
                                if is_causal:
                                    temp_mask = torch.ones(
                                        attn_mask_shape, dtype=torch.bool
                                    ).tril(diagonal=0)
                                    attn_bias.masked_fill_(
                                        temp_mask.logical_not(), float("-inf")
                                    )
                                    attn_bias.to(q2.dtype)
                                if mask2.dtype == torch.bool:
                                    attn_bias.masked_fill_(
                                        mask2.logical_not(), float("-inf")
                                    )
                                else:
                                    attn_bias += mask2
                                attn_weight = q2 @ k2.transpose(-2, -1) * scale_factor
                                attn_weight += attn_bias
                                attn_weight = torch.softmax(attn_weight, dim=-1)
                                math_ref = attn_weight @ v2

                            if dtype in [torch.bfloat16, torch.float16]:
                                math_ref = math_ref.to(dtype)
                                torch.testing.assert_close(
                                    actual, math_ref, atol=atol, rtol=rtol
                                )

    def test_flash_attention_stride0(self):
        input_shape = (
            1,
            16,
            1,
            48,
        )
        input_stride = (
            0,
            48,
            0,
            1,
        )
        q = torch.randn(
            input_shape, device="cpu", dtype=torch.float32, requires_grad=False
        ).as_strided(input_shape, input_stride)
        k = torch.randn(
            input_shape, device="cpu", dtype=torch.float32, requires_grad=False
        ).as_strided(input_shape, input_stride)
        v = torch.randn(
            input_shape, device="cpu", dtype=torch.float32, requires_grad=False
        ).as_strided(input_shape, input_stride)
        atol = 1e-5
        rtol = 5e-6
        q2 = q.clone()
        k2 = k.clone()
        v2 = v.clone()
        actual = torch.ops.torch_ipex.flash_attention(q, k, v)[0]
        math_ref = torch._scaled_dot_product_attention_math(q2, k2, v2)[0]
        torch.testing.assert_close(actual, math_ref, atol=1e-5, rtol=5e-6)

    def test_prepare_4d_causal_attention_mask(self):
        for dtype in [torch.float32, torch.bfloat16]:
            for sliding_window in [10, 40]:
                for seq_len in [1, 32]:
                    inputs_embeds = torch.rand((1, seq_len, 768), dtype=dtype)
                    finfo_min = torch.finfo(dtype).min
                    past_key_values_length = 0
                    if seq_len == 1:
                        past_key_values_length = 32
                    attention_mask = torch.ones(
                        (1, past_key_values_length + seq_len), dtype=torch.long
                    )
                    output = torch.ops.torch_ipex.prepare_4d_causal_attention_mask(
                        attention_mask,
                        inputs_embeds,
                        torch.tensor(past_key_values_length).contiguous(),
                        torch.tensor(finfo_min).contiguous(),
                        sliding_window,
                    )
                    try:
                        from transformers.modeling_attn_mask_utils import (
                            _prepare_4d_causal_attention_mask,
                        )

                        output_ref = _prepare_4d_causal_attention_mask(
                            attention_mask,
                            (inputs_embeds.shape[0], inputs_embeds.shape[1]),
                            inputs_embeds,
                            past_key_values_length,
                            sliding_window,
                        )
                        self.assertEqual(output, output_ref)
                    except ImportError:
                        pass

    def _clone_inputs(self, inputs, dtype=None):
        inputs = [
            x.clone().to(dtype) if dtype is not None else x.clone() for x in inputs
        ]
        return inputs

    def test_causal_conv1d_update(self):
        def causal_conv1d_update(
            hidden_states, conv_states, conv_weights, conv_bias, activation
        ):
            conv_state = torch.roll(conv_states, shifts=-1, dims=-1)
            conv_state[..., -1] = hidden_states[:, :, 0]
            hidden_states = torch.sum(conv_state * conv_weights, dim=-1)
            hidden_states += conv_bias
            hidden_states = activation(hidden_states).unsqueeze(-1)
            return hidden_states, conv_state

        conv = nn.Conv1d(
            in_channels=8192,
            out_channels=8192,
            kernel_size=4,
            stride=1,
            padding=3,
            groups=8192,
        )
        act = torch.nn.SiLU()
        conv_state = torch.rand(1, 8192, 4)
        hidden_states = torch.rand(1, 8192, 1)
        conv_weights = conv.weight.view(conv.weight.shape[0], conv.weight.shape[2])
        conv_bias = conv.bias
        example_inputs = (hidden_states, conv_state, conv_weights, conv_bias)

        with torch.no_grad():
            inputs_ref_fp32 = self._clone_inputs(example_inputs, torch.float32)
            output_fp32_ref, conv_output_fp32_ref = causal_conv1d_update(
                *inputs_ref_fp32, act
            )
            input_ipex_fp32 = self._clone_inputs(example_inputs, torch.float32)
            output_fp32_ipex, conv_state_ipex = (
                torch.ops.torch_ipex.causal_conv1d_update(*input_ipex_fp32, True)
            )
            self.assertEqual(output_fp32_ref, output_fp32_ipex)
            self.assertEqual(conv_output_fp32_ref, conv_state_ipex)

        dtypes = [torch.bfloat16]
        if core.onednn_has_fp16_support():
            dtypes.append(torch.float16)

        for dtype in dtypes:
            with torch.no_grad(), torch.cpu.amp.autocast(
                enabled=True if dtype in [torch.bfloat16, torch.float16] else False,
                dtype=dtype,
            ):
                input_ref = self._clone_inputs(example_inputs, dtype)
                output_ref, conv_output_ref = causal_conv1d_update(
                    *input_ref,
                    act,
                )
                input_ipex = self._clone_inputs(example_inputs, dtype)
                output_ipex, conv_output_ipex = (
                    torch.ops.torch_ipex.causal_conv1d_update(*input_ipex, True)
                )
                self.assertEqual(
                    output_ipex,
                    output_fp32_ref,
                    torch.max(torch.abs(output_ref - output_fp32_ref)),
                )
                self.assertEqual(conv_output_ref, conv_output_ipex)

    def test_selective_scan(self):
        def selective_scan_ref(
            u,
            delta,
            A,
            B,
            C,
            D=None,
            z=None,
            delta_bias=None,
            delta_softplus=False,
            return_last_state=False,
        ):
            """
            u: r(B D L)
            delta: r(B D L)
            A: c(D N) or r(D N)
            B: c(D N) or r(B N L) or r(B N 2L) or r(B G N L) or (B G N L)
            C: c(D N) or r(B N L) or r(B N 2L) or r(B G N L) or (B G N L)
            D: r(D)
            z: r(B D L)
            delta_bias: r(D), fp32

            out: r(B D L)
            last_state (optional): r(B D dstate) or c(B D dstate)
            """
            dtype_in = u.dtype
            u = u.float()
            delta = delta.float()
            if delta_bias is not None:
                delta = delta + delta_bias[..., None].float()
            if delta_softplus:
                delta = F.softplus(delta)
            batch, dim, dstate = u.shape[0], A.shape[0], A.shape[1]
            is_variable_B = B.dim() >= 3
            is_variable_C = C.dim() >= 3
            if A.is_complex():
                if is_variable_B:
                    B = torch.view_as_complex(B.float().view(*B.shape[:-1], -1, 2))
                if is_variable_C:
                    C = torch.view_as_complex(C.float().view(*C.shape[:-1], -1, 2))
            else:
                B = B.float()
                C = C.float()
            x = A.new_zeros((batch, dim, dstate))
            ys = []
            deltaA = torch.exp(torch.einsum("bdl,dn->bdln", delta, A))
            if not is_variable_B:
                deltaB_u = torch.einsum("bdl,dn,bdl->bdln", delta, B, u)
            else:
                if B.dim() == 3:
                    deltaB_u = torch.einsum("bdl,bnl,bdl->bdln", delta, B, u)
                else:
                    B = B.repeat(1, dim // B.shape[1], 1, 1)
                    deltaB_u = torch.einsum("bdl,bdnl,bdl->bdln", delta, B, u)
            if is_variable_C and C.dim() == 4:
                C = C.repeat(1, dim // C.shape[1], 1, 1)
            last_state = None
            for i in range(u.shape[2]):
                x = deltaA[:, :, i] * x + deltaB_u[:, :, i]
                if not is_variable_C:
                    y = torch.einsum("bdn,dn->bd", x, C)
                else:
                    if C.dim() == 3:
                        y = torch.einsum("bdn,bn->bd", x, C[:, :, i])
                    else:
                        y = torch.einsum("bdn,bdn->bd", x, C[:, :, :, i])
                if i == u.shape[2] - 1:
                    last_state = x
                if y.is_complex():
                    y = y.real * 2
                ys.append(y)
            y = torch.stack(ys, dim=2)  # (batch dim L)
            out = y if D is None else y + u * D.unsqueeze(-1)
            if z is not None:
                out = out * F.silu(z)
            out = out.to(dtype=dtype_in)
            return out if not return_last_state else (out, last_state)

        hidden_states = torch.rand(1, 8192, 1)
        discrete_time_step = torch.rand(1, 8192, 1)
        A = torch.rand(8192, 16)
        B = torch.rand(1, 1, 16)
        C = torch.rand(1, 1, 16)
        D = torch.ones(8192)
        gate = torch.rand(1, 8192, 1)
        time_proj_bias = torch.rand(8192)
        dtypes = [torch.float, torch.bfloat16]
        if core.onednn_has_fp16_support():
            dtypes.append(torch.float16)

        for dtype in dtypes:
            example_inputs = (
                hidden_states,
                discrete_time_step.to(torch.float),
                A,
                B.transpose(1, 2),
                C.transpose(1, 2),
                D,
                gate,
                time_proj_bias,
            )
            rtol, atol = (6e-4, 2e-3) if dtype == torch.float32 else (3e-3, 5e-3)
            if dtype == torch.bfloat16:
                rtol, atol = 3e-2, 5e-2
            rtolw, atolw = (1e-3, 1e-3)
            rtolw = max(rtolw, rtol)
            atolw = max(atolw, atol)
            with torch.no_grad(), torch.cpu.amp.autocast(
                enabled=True if dtype in [torch.bfloat16, torch.float16] else False,
                dtype=dtype,
            ):
                input_ref = self._clone_inputs(example_inputs)
                scan_outputs_ref, ssm_state_ref = selective_scan_ref(
                    *input_ref,
                    delta_softplus=True,
                    return_last_state=True,
                )
                input_ipex = self._clone_inputs(example_inputs)
                scan_outputs_ipex, ssm_state_ipex = (
                    torch.ops.torch_ipex.selective_scan_fn(
                        *input_ipex,
                        delta_softplus=True,
                        return_last_state=True,
                    )
                )
                self.assertTrue(
                    torch.allclose(
                        scan_outputs_ref,
                        scan_outputs_ipex,
                        rtol=rtolw,
                        atol=atolw,
                    )
                )
                self.assertTrue(
                    torch.allclose(
                        ssm_state_ref,
                        ssm_state_ipex,
                        rtol=rtolw,
                        atol=atolw,
                    )
                )

    def test_selective_state_update(self):
        def selective_state_update_ref(
            state, x, dt, A, B, C, D=None, z=None, dt_bias=None, dt_softplus=False
        ):
            """
            Argument:
                state: (batch, dim, dstate) or (batch, nheads, dim, dstate)
                x: (batch, dim) or (batch, nheads, dim)
                dt: (batch, dim) or (batch, nheads, dim)
                A: (dim, dstate) or (nheads, dim, dstate)
                B: (batch, dstate) or (batch, ngroups, dstate)
                C: (batch, dstate) or (batch, ngroups, dstate)
                D: (dim,) or (nheads, dim)
                z: (batch, dim) or (batch, nheads, dim)
                dt_bias: (dim,) or (nheads, dim)
            Return:
                out: (batch, dim) or (batch, nheads, dim)
            """
            has_heads = state.dim() > 3
            if state.dim() == 3:
                state = state.unsqueeze(1)
            if x.dim() == 2:
                x = x.unsqueeze(1)
            if dt.dim() == 2:
                dt = dt.unsqueeze(1)
            if A.dim() == 2:
                A = A.unsqueeze(0)
            if B.dim() == 2:
                B = B.unsqueeze(1)
            if C.dim() == 2:
                C = C.unsqueeze(1)
            if D is not None and D.dim() == 1:
                D = D.unsqueeze(0)
            if z is not None and z.dim() == 2:
                z = z.unsqueeze(1)
            if dt_bias is not None and dt_bias.dim() == 1:
                dt_bias = dt_bias.unsqueeze(0)
            batch, nheads, dim, dstate = state.shape
            assert x.shape == (batch, nheads, dim)
            assert dt.shape == x.shape
            assert A.shape == (nheads, dim, dstate)
            ngroups = B.shape[1]
            assert nheads % ngroups == 0, "nheads must be divisible by ngroups"
            assert B.shape == (batch, ngroups, dstate)
            assert C.shape == B.shape
            if D is not None:
                assert D.shape == (nheads, dim)
            if z is not None:
                assert z.shape == x.shape
            if dt_bias is not None:
                assert dt_bias.shape == (nheads, dim)
                dt = dt + dt_bias
            dt = F.softplus(dt) if dt_softplus else dt
            dA = torch.exp(dt[:, :, :, None] * A)  # (batch, nheads, dim, dstate)
            B = B.repeat(1, nheads // ngroups, 1)  # (batch, nheads, dstate)
            C = C.repeat(1, nheads // ngroups, 1)  # (batch, nheads, dstate)
            dB = dt[:, :, :, None] * B[:, :, None, :]  # (batch, nheads, dim, dstate)
            state.copy_(state * dA + dB * x[:, :, :, None])  # (batch, dim, dstate
            out = torch.einsum("bhdn,bhn->bhd", state.to(C.dtype), C)
            if D is not None:
                out += (x * D).to(out.dtype)
            out = (out if z is None else out * F.silu(z)).to(x.dtype)
            if not has_heads:
                out = out.squeeze(1)
            return out

        ssm_state = torch.rand(1, 8192, 16)
        hidden_states = torch.rand(1, 8192)
        discrete_time_step = torch.rand(1, 8192)
        A = torch.rand(8192, 16)
        B = torch.rand(1, 16)
        C = torch.rand(1, 16)
        D = torch.ones(8192)
        gate = torch.rand(1, 8192)
        time_proj_bias = torch.rand(8192)
        dtypes = [torch.float, torch.bfloat16]
        if core.onednn_has_fp16_support():
            dtypes.append(torch.float16)

        for dtype in dtypes:
            example_inputs = (
                ssm_state.to(dtype),
                hidden_states.to(dtype),
                discrete_time_step.to(dtype),
                A,
                B,
                C,
                D,
                gate.to(dtype),
                time_proj_bias,
            )
            rtol, atol = (3e-4, 1e-3) if dtype == torch.float32 else (3e-3, 5e-3)
            if dtype == torch.bfloat16:
                rtol, atol = 1e-2, 5e-2
            with torch.no_grad(), torch.cpu.amp.autocast(
                enabled=True if dtype in [torch.bfloat16, torch.float16] else False,
                dtype=dtype,
            ):
                input_ref = self._clone_inputs(example_inputs)
                output_ref = selective_state_update_ref(*input_ref, dt_softplus=True)
                input_ipex = self._clone_inputs(example_inputs)
                output_ipex = torch.ops.torch_ipex.selective_state_update(
                    *input_ipex, True
                )
                self.assertTrue(
                    torch.allclose(
                        output_ref,
                        output_ipex,
                        rtol=rtol,
                        atol=atol,
                    )
                )

    def test_deepseek_moe(self):
        class DeepseekV2MLP(nn.Module):
            def __init__(self, hidden_size=5120, intermediate_size=12288):
                super().__init__()
                self.hidden_size = hidden_size
                self.intermediate_size = intermediate_size

                self.gate_proj = nn.Linear(
                    self.hidden_size, self.intermediate_size, bias=False
                )
                self.up_proj = nn.Linear(
                    self.hidden_size, self.intermediate_size, bias=False
                )
                self.down_proj = nn.Linear(
                    self.intermediate_size, self.hidden_size, bias=False
                )
                self.act_fn = nn.SiLU()

            def forward(self, x):
                down_proj = self.down_proj(
                    self.act_fn(self.gate_proj(x)) * self.up_proj(x)
                )
                return down_proj

        class MoETest(nn.Module):
            def __init__(
                self,
                num_experts,
                hidden_size,
                intermediate_size,
                moe_linear_type=0,
                use_ipex=False,
            ):
                super().__init__()
                self.experts = nn.ModuleList(
                    [
                        DeepseekV2MLP(hidden_size, intermediate_size)
                        for _ in range(num_experts)
                    ]
                )
                self.use_ipex = use_ipex
                if use_ipex:
                    self.moe_linear_type = moe_linear_type
                    self.distributed = False
                    # 0: Default, 1: TPP, 2: DNNL, 3: MKL, 4: WOQ
                    if self.moe_linear_type == 1:
                        from intel_extension_for_pytorch.cpu._auto_kernel_selection import (
                            _enable_tpp,
                            _disable_tpp,
                        )

                        _enable_tpp()
                        self.experts = ipex.optimize(
                            self.experts.eval(), dtype=torch.bfloat16
                        )
                        _disable_tpp()
                    if self.moe_linear_type == 2:
                        self.experts = ipex.optimize(
                            self.experts.eval(), dtype=torch.bfloat16
                        )
                    if self.moe_linear_type == 3:
                        ipex._disable_dnnl()
                        self.experts = ipex.optimize(
                            self.experts.eval(), dtype=torch.float32, level="O1"
                        )
                    if self.moe_linear_type == 4:
                        qconfig_mapping = (
                            ipex.quantization.get_weight_only_quant_qconfig_mapping()
                        )
                        from intel_extension_for_pytorch.quantization import (
                            prepare,
                            convert,
                        )

                        self.experts = copy.deepcopy(self.experts)
                        self.experts = prepare(
                            self.experts, qconfig_mapping, inplace=True
                        )
                        self.experts = convert(self.experts, inplace=True)
                    self.gate_weights = []
                    self.up_weights = []
                    self.down_weights = []
                    self.gate_ctx = []
                    self.up_ctx = []
                    self.down_ctx = []
                    for expert_idx in range(len(self.experts)):
                        expert_layer = self.experts[expert_idx]
                        if self.moe_linear_type in [0, 1]:
                            self.gate_weights.append(expert_layer.gate_proj.weight)
                            self.up_weights.append(expert_layer.up_proj.weight)
                            self.down_weights.append(expert_layer.down_proj.weight)
                        elif self.moe_linear_type in [2, 3]:
                            self.gate_weights.append(
                                expert_layer.gate_proj._get_forward_weight()
                            )
                            self.up_weights.append(
                                expert_layer.up_proj._get_forward_weight()
                            )
                            self.down_weights.append(
                                expert_layer.down_proj._get_forward_weight()
                            )
                            self.gate_ctx.append(expert_layer.gate_proj.ctx)
                            self.up_ctx.append(expert_layer.up_proj.ctx)
                            self.down_ctx.append(expert_layer.down_proj.ctx)
                        else:
                            self.gate_ctx.append(expert_layer.gate_proj._op_context)
                            self.up_ctx.append(expert_layer.up_proj._op_context)
                            self.down_ctx.append(expert_layer.down_proj._op_context)

            def forward(self, x, topk_ids, topk_weight):
                if self.use_ipex:
                    return self.moe_infer_ipex(x, topk_ids, topk_weight)
                return self.moe_infer_ref(x, topk_ids, topk_weight)

            def moe_infer_ref(self, x, topk_ids, topk_weight):
                cnts = topk_ids.new_zeros((topk_ids.shape[0], len(self.experts)))
                cnts.scatter_(1, topk_ids, 1)
                tokens_per_expert = cnts.sum(dim=0)
                idxs = topk_ids.view(-1).argsort()
                sorted_tokens = x[idxs // topk_ids.shape[1]]
                tokens_per_expert = tokens_per_expert.cpu().numpy()

                outputs = []
                start_idx = 0
                for i, num_tokens in enumerate(tokens_per_expert):
                    end_idx = start_idx + num_tokens
                    if num_tokens == 0:
                        continue
                    expert = self.experts[i]
                    tokens_for_this_expert = sorted_tokens[start_idx:end_idx]
                    expert_out = expert(tokens_for_this_expert)
                    outputs.append(expert_out)
                    start_idx = end_idx

                outs = (
                    torch.cat(outputs, dim=0)
                    if len(outputs)
                    else sorted_tokens.new_empty(0)
                )
                new_x = torch.empty_like(outs)
                new_x[idxs] = outs
                final_out = (
                    new_x.view(*topk_ids.shape, -1)
                    .type(topk_weight.dtype)
                    .mul_(topk_weight.unsqueeze(dim=-1))
                    .sum(dim=1)
                    .type(new_x.dtype)
                )
                return final_out

            def moe_infer_ipex(self, x, topk_ids, topk_weight):
                if self.moe_linear_type in [0, 1]:
                    final_out = torch.ops.torch_ipex.deepseek_moe_tpp(
                        x,
                        topk_ids,
                        self.gate_weights,
                        self.up_weights,
                        self.down_weights,
                        self.moe_linear_type == 0,
                        topk_weight,
                        self.distributed,
                    )
                elif self.moe_linear_type == 2:
                    final_out = torch.ops.torch_ipex.deepseek_moe(
                        x,
                        topk_ids,
                        self.gate_weights,
                        self.gate_ctx,
                        self.up_weights,
                        self.up_ctx,
                        self.down_weights,
                        self.down_ctx,
                        topk_weight,
                        self.distributed,
                    )
                elif self.moe_linear_type == 3:
                    final_out = torch.ops.torch_ipex.deepseek_moe_mkl(
                        x,
                        topk_ids,
                        self.gate_weights,
                        self.gate_ctx,
                        self.up_weights,
                        self.up_ctx,
                        self.down_weights,
                        self.down_ctx,
                        topk_weight,
                        self.distributed,
                    )
                else:
                    final_out = torch.ops.torch_ipex.deepseek_moe_woq(
                        x,
                        topk_ids,
                        self.gate_ctx,
                        self.up_ctx,
                        self.down_ctx,
                        topk_weight,
                        self.distributed,
                    )
                return final_out

        tokens = 1
        hidden_size = 64
        intermediate_size = 1024
        num_experts = 8
        selected_experts = 2
        x = torch.rand(tokens, hidden_size)
        topk_weight = torch.rand(tokens, selected_experts)
        topk_ids = torch.randint(0, num_experts, (tokens, selected_experts))
        with torch.no_grad():
            model_ref = MoETest(num_experts, hidden_size, intermediate_size).eval()
            output_ref = model_ref(x.clone(), topk_ids.clone(), topk_weight.clone())
            for moe_linear_type in [0, 1, 2, 3, 4]:
                amp_enabled = False if moe_linear_type == 3 else True
                dtype = torch.float32 if moe_linear_type == 3 else torch.bfloat16
                x_clone = x.clone().to(dtype)
                topk_ids_clone = topk_ids.clone()
                topk_weight_clone = topk_weight.clone().to(dtype)
                with torch.cpu.amp.autocast(enabled=amp_enabled):
                    model_ipex = MoETest(
                        num_experts,
                        hidden_size,
                        intermediate_size,
                        moe_linear_type,
                        True,
                    ).eval()
                    output_ipex = model_ipex(x_clone, topk_ids_clone, topk_weight_clone)
                    self.assertEqual(output_ref, output_ipex, prec=0.1)

    def test_deepseek_moegate(self):
        n_group = 8
        topk_group = 3
        n_routed_experts = 16
        top_k = 6
        routed_scaling_factor = 16.0

        def moe_gate(scores):
            n, h = scores.shape
            group_scores = (
                scores.view(n, n_group, -1).max(dim=-1).values
            )  # [n, n_group]
            group_idx = torch.topk(group_scores, k=topk_group, dim=-1, sorted=False)[
                1
            ]  # [n, top_k_group]
            group_mask = torch.zeros_like(group_scores)  # [n, n_group]
            group_mask.scatter_(1, group_idx, 1)  # [n, n_group]
            score_mask = (
                group_mask.unsqueeze(-1)
                .expand(n, n_group, n_routed_experts // n_group)
                .reshape(n, -1)
            )  # [n, e]
            tmp_scores = scores.masked_fill(~score_mask.bool(), 0.0)  # [n, e]
            topk_weight, topk_idx = torch.topk(
                tmp_scores, k=top_k, dim=-1, sorted=False
            )

            topk_weight = topk_weight * routed_scaling_factor
            return topk_idx, topk_weight

        dtypes = [torch.float32, torch.bfloat16]
        if core.onednn_has_fp16_support():
            dtypes.append(torch.float16)
        for dtype in dtypes:
            hidden_states = torch.rand(10, 2560, dtype=dtype)
            weight = torch.rand(16, 2560, dtype=dtype)
            logits = torch.nn.functional.linear(
                hidden_states.type(torch.float32), weight.type(torch.float32), None
            )
            scores = logits.softmax(dim=-1, dtype=dtype)
            enable_autocast = dtype == torch.bfloat16
            with torch.no_grad(), torch.cpu.amp.autocast(enabled=enable_autocast):
                topk_idx_ref, topk_weight_ref = moe_gate(scores)
                topk_idx_ipex, topk_weight_ipex = torch.ops.torch_ipex.deepseek_moegate(
                    hidden_states,
                    scores,
                    torch.tensor(routed_scaling_factor),
                    n_group,
                    topk_group,
                    n_routed_experts,
                    top_k,
                )
                self.assertEqual(topk_idx_ref, topk_idx_ipex)
                self.assertEqual(topk_weight_ref, topk_weight_ipex)

    def test_deepseekv3_moegate(self):
        n_group = 8
        topk_group = 3
        n_routed_experts = 16
        top_k = 6
        routed_scaling_factor = 16.0
        e_score_correction_bias = torch.rand(n_routed_experts)

        def moe_gate(scores):
            n, h = scores.shape
            scores_for_choice = scores.view(n, -1) + e_score_correction_bias.unsqueeze(
                0
            )
            group_scores = (
                scores_for_choice.view(n, n_group, -1).topk(2, dim=-1)[0].sum(dim=-1)
            )  # [n, n_group]
            group_idx = torch.topk(group_scores, k=topk_group, dim=-1, sorted=False)[
                1
            ]  # [n, top_k_group]
            group_mask = torch.zeros_like(group_scores)  # [n, n_group]
            group_mask.scatter_(1, group_idx, 1)  # [n, n_group]
            score_mask = (
                group_mask.unsqueeze(-1)
                .expand(n, n_group, n_routed_experts // n_group)
                .reshape(n, -1)
            )  # [n, e]
            tmp_scores = scores_for_choice.masked_fill(
                ~score_mask.bool(), 0.0
            )  # [n, e]
            _, topk_idx = torch.topk(tmp_scores, k=top_k, dim=-1, sorted=False)
            topk_weight = scores.gather(1, topk_idx)

            return topk_idx, topk_weight

        dtypes = [torch.float32, torch.bfloat16]
        if core.onednn_has_fp16_support():
            dtypes.append(torch.float16)
        for dtype in dtypes:
            hidden_states = torch.rand(10, 2560, dtype=dtype)
            weight = torch.rand(16, 2560, dtype=dtype)
            logits = torch.nn.functional.linear(
                hidden_states.type(torch.float32), weight.type(torch.float32), None
            )
            scores = logits.sigmoid()
            enable_autocast = dtype == torch.bfloat16
            with torch.no_grad(), torch.cpu.amp.autocast(enabled=enable_autocast):
                topk_idx_ref, topk_weight_ref = moe_gate(scores)
                topk_idx_ipex, topk_weight_ipex = torch.ops.torch_ipex.deepseek_moegate(
                    hidden_states,
                    scores.to(dtype),
                    torch.tensor(routed_scaling_factor),
                    n_group,
                    topk_group,
                    n_routed_experts,
                    top_k,
                    torch.tensor(e_score_correction_bias),
                )
                self.assertEqual(topk_idx_ref, topk_idx_ipex)
                self.assertEqual(topk_weight_ref, topk_weight_ipex)


if __name__ == "__main__":
    test = unittest.main()
