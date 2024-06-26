import torch
import torch.nn as nn
import torchvision
import torch.backends.cudnn as cudnn
import torch.optim
import os
import sys
import argparse
import time
import dataloader
import model
import Myloss
import numpy as np
from torchvision import transforms
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torch.cuda.amp import autocast, GradScaler
from skimage.metrics import structural_similarity as ssim

def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        m.weight.data.normal_(0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)


def train(config):

    os.environ['CUDA_VISIBLE_DEVICES'] = '0'

    cudnn.benchmark = True

    scale_factor = config.scale_factor
    DCE_net = model.enhance_net_nopool(scale_factor).cuda()

    if config.load_pretrain:
        DCE_net.load_state_dict(torch.load(config.pretrain_dir))

    train_dataset = dataloader.lowlight_loader(config.lowlight_images_path)

    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=config.train_batch_size, shuffle=True,
                                               num_workers=config.num_workers, pin_memory=True)

    L_color = Myloss.L_color()
    L_spa = Myloss.L_spa()
    L_exp = Myloss.L_exp(16)
    L_TV = Myloss.L_TV()

    optimizer = torch.optim.Adam(DCE_net.parameters(), lr=config.lr, weight_decay=config.weight_decay)
    scheduler = ReduceLROnPlateau(optimizer, 'min', patience=5)

    scaler = GradScaler()

    DCE_net.train()

    for epoch in range(config.num_epochs):
        total_loss = 0.0

        for iteration, img_lowlight in enumerate(train_loader):
            img_lowlight = img_lowlight.cuda()

            with autocast():
                E = 0.6
                enhanced_image, A = DCE_net(img_lowlight)
                Loss_TV = 1600 * L_TV(A)
                loss_spa = torch.mean(L_spa(enhanced_image, img_lowlight))
                loss_col = 5 * torch.mean(L_color(enhanced_image))
                loss_exp = 10 * torch.mean(L_exp(enhanced_image, E))
                loss = Loss_TV + loss_spa + loss_col + loss_exp

            optimizer.zero_grad()
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()

            total_loss += loss.item()

            if ((iteration + 1) % config.display_iter) == 0:
                print("Epoch [{}/{}], Iteration [{}/{}], Loss: {:.4f}"
                      .format(epoch+1, config.num_epochs, iteration+1, len(train_loader), loss.item()))

        avg_loss = total_loss / len(train_loader)
        print("Average Loss for Epoch {}: {:.4f}".format(epoch+1, avg_loss))
        scheduler.step(avg_loss)

        if ((epoch + 1) % config.snapshot_epoch) == 0:
            torch.save(DCE_net.state_dict(), config.snapshots_folder + "Epoch" + str(epoch) + '.pth')


def evaluate_model(model, dataloader):
    model.eval()
    ssim_score = 0.0
    with torch.no_grad():
        for img_lowlight in dataloader:
            img_lowlight = img_lowlight.cuda()
            enhanced_image, _ = model(img_lowlight)
            for img_enhanced, img_orig in zip(enhanced_image, img_lowlight):
                ssim_score += ssim(img_enhanced.cpu().numpy(), img_orig.cpu().numpy(), multichannel=True)
    return ssim_score / len(dataloader.dataset)


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    # Input Parameters
    parser.add_argument('--lowlight_images_path', type=str, default="/content/Zero-DCE_extension/Zero-DCE++/data/train_data/")
    parser.add_argument('--lr', type=float, default=0.0001)
    parser.add_argument('--weight_decay', type=float, default=0.0001)
    parser.add_argument('--grad_clip_norm', type=float, default=0.1)
    parser.add_argument('--num_epochs', type=int, default=100)
    parser.add_argument('--train_batch_size', type=int, default=8)
    parser.add_argument('--val_batch_size', type=int, default=8)
    parser.add_argument('--num_workers', type=int, default=4)
    parser.add_argument('--display_iter', type=int, default=10)
    parser.add_argument('--scale_factor', type=int, default=1)
    parser.add_argument('--snapshots_folder', type=str, default="/content/Zero-DCE_extension/Zero-DCE++/snapshots_Zero_DCE++")
    parser.add_argument('--load_pretrain', type=bool, default=False)
    parser.add_argument('--pretrain_dir', type=str, default="/content/Zero-DCE_extension/Zero-DCE++/snapshots_Zero_DCE++/Epoch99.pth")
    parser.add_argument('--snapshot_epoch', type=int, default=10)

    config = parser.parse_args()

    if not os.path.exists(config.snapshots_folder):
        os.mkdir(config.snapshots_folder)

    train(config)
