import time
from datetime import datetime
import os
import json

import random
from torchvision.utils import make_grid
from einops import rearrange
import pandas as pd
import cv2
import numpy as np
from PIL import Image
import pathlib
import torchvision.transforms as T

from .generate import generate, generate_lowmem, add_noise
from .prompt import sanitize
from .animation import DeformAnimKeys, sample_from_cv2, sample_to_cv2, anim_frame_warp_2d, anim_frame_warp_3d, vid2frames
from .depth import DepthModel
from .colors import maintain_colors

from .display_emu import display

from backend.singleton import singleton
gs = singleton
def next_seed(args):
    print(type(args.seed))
    print(args.seed)
    args.seed = int(args.seed)
    if args.seed_behavior == 'iter':
        args.seed += 1
    elif args.seed_behavior == 'fixed':
        pass # always keep seed the same
    else:
        args.seed = random.randint(0, 2**32 - 1)
    return args.seed


def save_settings(args, outfolder, prompt, index):
    os.makedirs(outfolder, exist_ok=True)
    if args.save_settings or args.save_samples:
        print(f"Saving to {outfolder}_*")
    # save settings for the batch
    if args.save_settings:
        filename = os.path.join(outfolder, f"{args.timestring}_{index:05}_{sanitize(prompt)[:160]}_settings.txt")
        args.actual_prompt = prompt
        with open(filename, "w+", encoding="utf-8") as f:
            json.dump(dict(args.__dict__), f, ensure_ascii=False, indent=4)
        del args.actual_prompt


def render_image_batch(args, prompts, root, image_callback=None, step_callback=None):
    args.prompts = {k: f"{v:05d}" for v, k in enumerate(prompts)}

    # create output folder for the batch
    os.makedirs(args.outdir, exist_ok=True)
#moved into a function for having prompt in file name in folder
    """
    if args.save_settings or args.save_samples:
        print(f"Saving to {os.path.join(args.outdir, args.timestring)}_*")

    # save settings for the batch
    if args.save_settings:
        filename = os.path.join(args.outdir, f"{args.timestring}_settings.txt")
        with open(filename, "w+", encoding="utf-8") as f:
            json.dump(dict(args.__dict__), f, ensure_ascii=False, indent=4)
    """
    index = 0
    # function for init image batching
    init_array = []
    if args.use_init:
        if args.init_image == "":
            raise FileNotFoundError("No path was given for init_image")
        if args.init_image.startswith('http://') or args.init_image.startswith('https://'):
            init_array.append(args.init_image)
        elif not os.path.isfile(args.init_image):
            if args.init_image[-1] != "/": # avoids path error by adding / to end if not there
                args.init_image += "/" 
            for image in sorted(os.listdir(args.init_image)): # iterates dir and appends images to init_array
                if image.split(".")[-1] in ("png", "jpg", "jpeg"):
                    init_array.append(args.init_image + image)
        else:
            init_array.append(args.init_image)
    else:
        init_array = [""]

    # when doing large batches don't flood browser with images
    clear_between_batches = args.n_batch >= 32
    fpW = args.W
    fpH = args.H
    timestring = datetime.now().strftime("%Y%m%d-%H%M%S")
    paths = []
    for iprompt, prompt in enumerate(prompts):
        #prevent empty prompts from gernerating images
        #if gs.stop_all:
        #    return paths
        if prompt != '':
            args.prompt = prompt
            args.clip_prompt = prompt

            all_images = []

            for batch_index in range(args.n_batch):
                #no display here
                #if clear_between_batches and batch_index % 32 == 0:
                #    display.clear_output(wait=True)
                print(f"Batch {batch_index+1} of {args.n_batch}")

                for image in init_array: # iterates the init images
                    if not gs.stop_all:
                        args.init_image = image
                        if args.hires == True:
                            args.use_init = False
                            args.init_sample = None
                            args.init_latent = None
                            args.init_c = None
                            if args.lowmem == True:
                                sample = generate_lowmem(args, root, return_sample=True, step_callback=step_callback,
                                                         hires=True)
                            else:
                                sample = generate(args, root, return_sample=True, step_callback=step_callback,
                                                         hires=True)

                            args.init_sample = sample[0]
                            args.use_init = True
                            args.strength = args.hiresstr
                            args.W = fpW
                            args.H = fpH
                            if args.lowmem == True:
                                results = generate_lowmem(args, root, step_callback=step_callback)
                            else:
                                results = generate(args, root, step_callback=step_callback,)
                            args.init_latent = None
                            args.init_sample = None
                            args.strength = 0
                            args.use_init = gs.diffusion.use_init
                        else:
                            if args.lowmem == True:
                                results = generate_lowmem(args, root, step_callback=step_callback)
                            else:
                                results = generate(args, root, step_callback=step_callback,)
                    if results is not None:
                        for image in results:
                            if args.make_grid:
                                all_images.append(T.functional.pil_to_tensor(image))
                            if args.save_samples:
                                if args.filename_format == "{timestring}_{index}_{prompt}.png":
                                    filename = f"{timestring}_{index:05}_{sanitize(prompt)[:160]}.png"
                                else:
                                    filename = f"{timestring}_{index:05}_{args.seed}.png"
                                #added prompt to output folder name
                                if gs.system.pathmode == "subfolders":
                                    outfolder = os.path.join(args.outdir, f'{timestring}_{sanitize(prompt)[:120]}')
                                else:
                                    outfolder = os.path.join(args.outdir, datetime.now().strftime("%Y%m%d"))
                                os.makedirs(outfolder, exist_ok=True)
                                outpath = os.path.join(outfolder, filename)
                                paths.append(outpath)
                                image.save(outpath)
                                args.init_sample = None
                                if args.save_settings == True:
                                    #print(args)
                                    params = args
                                    for key, value in params.__dict__.items():
                                        params.__dict__[key] = str(params.__dict__[key])
                                    save_settings(params, outfolder, prompt, index)
                                    del params
                                #Callback Mod
                                if image_callback is not None:
                                    image_callback(image)
                            if args.display_samples:
                                display.display(image)
                            index += 1
                        args.seed = str(args.seed)
                        args.seed = next_seed(args)

                #print(len(all_images))
                if args.make_grid == True:
                    grid = make_grid(all_images, nrow=int(len(all_images)/args.grid_rows))
                    grid = rearrange(grid, 'c h w -> h w c').cpu().numpy()
                    filename = f"{args.timestring}_{iprompt:05d}_grid_{args.seed}.png"
                    grid_image = Image.fromarray(grid.astype(np.uint8))
                    grid_image.save(os.path.join(args.outdir, filename))
                    display.clear_output(wait=True)
                    display.display(grid_image)
        return paths


def render_animation(args, anim_args, animation_prompts, root, image_callback=None, step_callback=None,
                     save_depth_maps=None):
    # animations use key framed prompts
    args.prompts = animation_prompts

    # expand key frame strings to values
    keys = DeformAnimKeys(anim_args)

    # resume anima![](../../../output/txt2img/20221113/20221113191718_00000_terrifying_sea_creature_big_teeth_ominous_scary_horror_realistic_digital_art_photorealism_trending_on_artstation_.png)tion
    start_frame = 0
    if anim_args.resume_from_timestring:
        for tmp in os.listdir(args.outdir):
            if tmp.split("_")[0] == anim_args.resume_timestring:
                start_frame += 1
        start_frame = start_frame - 1

    # create output folder for the batch
    os.makedirs(args.outdir, exist_ok=True)
    print(f"Saving animation frames to {args.outdir}")

    # save settings for the batch
    settings_filename = os.path.join(args.outdir, f"{args.timestring}_settings.txt")
    with open(settings_filename, "w+", encoding="utf-8") as f:
        s = {**dict(args.__dict__), **dict(anim_args.__dict__)}
        json.dump(s, f, ensure_ascii=False, indent=4)
        
    # resume from timestring
    if anim_args.resume_from_timestring:
        args.timestring = anim_args.resume_timestring

    # expand prompts out to per-frame
    prompt_series = pd.Series([np.nan for a in range(anim_args.max_frames)])
    for i, prompt in animation_prompts.items():
        prompt_series[i] = prompt
    prompt_series = prompt_series.ffill().bfill()

    # check for video inits
    using_vid_init = anim_args.animation_mode == 'Video Input'

    # load depth model for 3D
    #predict_depths = (anim_args.animation_mode == '3D' and anim_args.use_depth_warping) or anim_args.save_depth_maps
    cpudepth = False
    adabins = False
    predict_depths = anim_args.animation_mode == '3D' or anim_args.use_depth_warping or anim_args.save_depth_maps
    if predict_depths:
        if cpudepth == True:
            print("Loading depth models to cpu")
            depth_model = DepthModel('cpu')
        else:
            depth_model = DepthModel('cuda')

        depth_model.load_midas(models_path=gs.system.support_models)
        if anim_args.midas_weight < 1.0:
            if adabins:
                if "adabins" not in gs.models:
                    depth_model.load_adabins(models_path=gs.system.support_models)
            else:
                gs.models["adabins"] = None
    else:
        depth_model = None
        anim_args.save_depth_maps = False
    """if predict_depths:
        depth_model = DepthModel(root.device)
        depth_model.load_midas(root.models_path)
        if anim_args.midas_weight < 1.0:
            depth_model.load_adabins(root.models_path)
    else:
        depth_model = None
        anim_args.save_depth_maps = False"""

    # state for interpolating between diffusion steps
    turbo_steps = 1 if using_vid_init else int(anim_args.diffusion_cadence)
    turbo_prev_image, turbo_prev_frame_idx = None, 0
    turbo_next_image, turbo_next_frame_idx = None, 0

    # resume animation
    prev_sample = None
    color_match_sample = None
    if anim_args.resume_from_timestring:
        last_frame = start_frame-1
        if turbo_steps > 1:
            last_frame -= last_frame%turbo_steps
        path = os.path.join(args.outdir,f"{args.timestring}_{last_frame:05}.png")
        img = cv2.imread(path)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        prev_sample = sample_from_cv2(img)
        if anim_args.color_coherence != 'None':
            color_match_sample = img
        if turbo_steps > 1:
            turbo_next_image, turbo_next_frame_idx = sample_to_cv2(prev_sample, type=np.float32), last_frame
            turbo_prev_image, turbo_prev_frame_idx = turbo_next_image, turbo_next_frame_idx
            start_frame = last_frame+turbo_steps

    args.n_samples = 1
    frame_idx = start_frame
    #print(f"frame idx = {frame_idx}")
    #print(f"frame idx = {anim_args.max_frames}")
    while frame_idx < anim_args.max_frames:
        if gs.stop_all:
            break
        print(f"Rendering animation frame {frame_idx} of {anim_args.max_frames}")
        noise = keys.noise_schedule_series[frame_idx]
        strength = keys.strength_schedule_series[frame_idx]
        contrast = keys.contrast_schedule_series[frame_idx]
        depth = None
        
        # emit in-between frames
        if turbo_steps > 1:
            tween_frame_start_idx = max(0, frame_idx-turbo_steps)
            for tween_frame_idx in range(tween_frame_start_idx, frame_idx):
                tween = float(tween_frame_idx - tween_frame_start_idx + 1) / float(frame_idx - tween_frame_start_idx)
                print(f"  creating in between frame {tween_frame_idx} tween:{tween:0.2f}")

                advance_prev = turbo_prev_image is not None and tween_frame_idx > turbo_prev_frame_idx
                advance_next = tween_frame_idx > turbo_next_frame_idx

                if depth_model is not None:
                    assert(turbo_next_image is not None)
                    depth = depth_model.predict(turbo_next_image, anim_args)

                if anim_args.animation_mode == '2D':
                    if advance_prev:
                        turbo_prev_image = anim_frame_warp_2d(turbo_prev_image, args, anim_args, keys, tween_frame_idx)
                    if advance_next:
                        turbo_next_image = anim_frame_warp_2d(turbo_next_image, args, anim_args, keys, tween_frame_idx)
                else: # '3D'
                    if advance_prev:
                        turbo_prev_image = anim_frame_warp_3d(root.device, turbo_prev_image, depth, anim_args, keys, tween_frame_idx)
                    if advance_next:
                        turbo_next_image = anim_frame_warp_3d(root.device, turbo_next_image, depth, anim_args, keys, tween_frame_idx)
                turbo_prev_frame_idx = turbo_next_frame_idx = tween_frame_idx

                if turbo_prev_image is not None and tween < 1.0:
                    img = turbo_prev_image*(1.0-tween) + turbo_next_image*tween
                else:
                    img = turbo_next_image
                #if image_callback is not None:
                #    image_callback(image)
                filename = f"{args.timestring}_{tween_frame_idx:05}.png"
                filepath = os.path.join(args.outdir, filename)
                cv2.imwrite(os.path.join(args.outdir, filename), cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_RGB2BGR))
                if image_callback is not None:
                    image_callback(Image.open(filepath))
                if anim_args.save_depth_maps:
                    depth_model.save(os.path.join(args.outdir, f"{args.timestring}_depth_{tween_frame_idx:05}.png"), depth)
            if turbo_next_image is not None:
                prev_sample = sample_from_cv2(turbo_next_image)

        # apply transforms to previous frame
        if prev_sample is not None:
            if anim_args.animation_mode == '2D':
                prev_img = anim_frame_warp_2d(sample_to_cv2(prev_sample), args, anim_args, keys, frame_idx)
            else: # '3D'
                prev_img_cv2 = sample_to_cv2(prev_sample)
                depth = depth_model.predict(prev_img_cv2, anim_args) if depth_model else None
                prev_img = anim_frame_warp_3d(root.device, prev_img_cv2, depth, anim_args, keys, frame_idx)

            # apply color matching
            if anim_args.color_coherence != 'None':
                if color_match_sample is None:
                    color_match_sample = prev_img.copy()
                else:
                    prev_img = maintain_colors(prev_img, color_match_sample, anim_args.color_coherence)

            # apply scaling
            contrast_sample = prev_img * contrast
            # apply frame noising
            noised_sample = add_noise(sample_from_cv2(contrast_sample), noise)

            # use transformed previous frame as init for current
            args.use_init = True
            if root.half_precision:
                args.init_sample = noised_sample.half().to(root.device)
            else:
                args.init_sample = noised_sample.to(root.device)
            args.strength = max(0.0, min(1.0, strength))

        # grab prompt for current frame
        args.prompt = prompt_series[frame_idx]
        print(f"{args.prompt} {args.seed}")
        if not using_vid_init:
            print(f"Angle: {keys.angle_series[frame_idx]} Zoom: {keys.zoom_series[frame_idx]}")
            print(f"Tx: {keys.translation_x_series[frame_idx]} Ty: {keys.translation_y_series[frame_idx]} Tz: {keys.translation_z_series[frame_idx]}")
            print(f"Rx: {keys.rotation_3d_x_series[frame_idx]} Ry: {keys.rotation_3d_y_series[frame_idx]} Rz: {keys.rotation_3d_z_series[frame_idx]}")

        # grab init image for current frame
        if using_vid_init:
            init_frame = os.path.join(args.outdir, 'inputframes', f"{frame_idx+1:05}.jpg")            
            print(f"Using video init frame {init_frame}")
            args.init_image = init_frame
            if anim_args.use_mask_video:
                mask_frame = os.path.join(args.outdir, 'maskframes', f"{frame_idx+1:05}.jpg")
                args.mask_file = mask_frame

        # sample the diffusion model
        #print(f"{args.init_sample}")
        sample, image = generate(args, root, frame_idx, return_latent=False, return_sample=True, step_callback=step_callback)
        if not using_vid_init:
            prev_sample = sample

        if turbo_steps > 1:
            turbo_prev_image, turbo_prev_frame_idx = turbo_next_image, turbo_next_frame_idx
            turbo_next_image, turbo_next_frame_idx = sample_to_cv2(sample, type=np.float32), frame_idx
            frame_idx += turbo_steps
        else:    
            filename = f"{args.timestring}_{frame_idx:05}.png"
            if image_callback is not None and anim_args.diffusion_cadence < 2:
                image_callback(image)
            image.save(os.path.join(args.outdir, filename))
            if anim_args.save_depth_maps:
                if depth is None:
                    depth = depth_model.predict(sample_to_cv2(sample), anim_args)
                depth_model.save(os.path.join(args.outdir, f"{args.timestring}_depth_{frame_idx:05}.png"), depth)
            frame_idx += 1

        #display.clear_output(wait=True)
        #display.display(image)
        #if image_callback is not None:
        #    image_callback(image)
        args.seed = next_seed(args)

def render_input_video(args, anim_args, animation_prompts, root, image_callback=None):
    # create a folder for the video input frames to live in
    video_in_frame_path = os.path.join(args.outdir, 'inputframes') 
    os.makedirs(video_in_frame_path, exist_ok=True)
    
    # save the video frames from input video
    print(f"Exporting Video Frames (1 every {anim_args.extract_nth_frame}) frames to {video_in_frame_path}...")
    vid2frames(anim_args.video_init_path, video_in_frame_path, anim_args.extract_nth_frame, anim_args.overwrite_extracted_frames)

    # determine max frames from length of input frames
    anim_args.max_frames = len([f for f in pathlib.Path(video_in_frame_path).glob('*.jpg')])
    args.use_init = True
    print(f"Loading {anim_args.max_frames} input frames from {video_in_frame_path} and saving video frames to {args.outdir}")

    if anim_args.use_mask_video:
        # create a folder for the mask video input frames to live in
        mask_in_frame_path = os.path.join(args.outdir, 'maskframes') 
        os.makedirs(mask_in_frame_path, exist_ok=True)

        # save the video frames from mask video
        print(f"Exporting Video Frames (1 every {anim_args.extract_nth_frame}) frames to {mask_in_frame_path}...")
        vid2frames(anim_args.video_mask_path, mask_in_frame_path, anim_args.extract_nth_frame, anim_args.overwrite_extracted_frames)
        args.use_mask = True
        args.overlay_mask = True

    render_animation(args, anim_args, animation_prompts, root, image_callback=image_callback)

def render_interpolation(args, anim_args, animation_prompts, root, image_callback=None, step_callback=None):
    # animations use key framed prompts
    args.prompts = animation_prompts

    # create output folder for the batch
    os.makedirs(args.outdir, exist_ok=True)
    print(f"Saving animation frames to {args.outdir}")

    # save settings for the batch
    settings_filename = os.path.join(args.outdir, f"{args.timestring}_settings.txt")
    with open(settings_filename, "w+", encoding="utf-8") as f:
        s = {**dict(args.__dict__), **dict(anim_args.__dict__)}
        json.dump(s, f, ensure_ascii=False, indent=4)
    
    # Interpolation Settings
    args.n_samples = 1
    args.seed_behavior = 'fixed' # force fix seed at the moment bc only 1 seed is available
    prompts_c_s = [] # cache all the text embeddings

    print(f"Preparing for interpolation of the following...")

    for i, prompt in animation_prompts.items():
        if gs.stop_all:
            return
        args.prompt = prompt
        args.clip_prompt = args.prompt

        # sample the diffusion model
        results = generate(args, root, return_c=True)
        c, image = results[0], results[1]
        prompts_c_s.append(c)

        # display.clear_output(wait=True)
        display.display(image)

        args.seed = next_seed(args)

    display.clear_output(wait=True)
    print(f"Interpolation start...")

    frame_idx = 0

    if anim_args.interpolate_key_frames:
        for i in range(len(prompts_c_s)-1):
            dist_frames = list(animation_prompts.items())[i+1][0] - list(animation_prompts.items())[i][0]
            if dist_frames <= 0:
                print("key frames duplicated or reversed. interpolation skipped.")
                return
        else:
            for j in range(dist_frames):
                # interpolate the text embedding
                prompt1_c = prompts_c_s[i]
                prompt2_c = prompts_c_s[i+1]  
                args.init_c = prompt1_c.add(prompt2_c.sub(prompt1_c).mul(j * 1/dist_frames))

                # sample the diffusion model
                results = generate(args)
                image = results[0]

                filename = f"{args.timestring}_{frame_idx:05}.png"
                image.save(os.path.join(args.outdir, filename))
                frame_idx += 1
                #Image callback mod
                if image_callback is not None:
                    image_callback(image)
                display.clear_output(wait=True)
                display.display(image)

                args.seed = next_seed(args)

    else:
        for i in range(len(prompts_c_s)-1):
            for j in range(anim_args.interpolate_x_frames+1):
                # interpolate the text embedding
                prompt1_c = prompts_c_s[i]
                prompt2_c = prompts_c_s[i+1]  
                args.init_c = prompt1_c.add(prompt2_c.sub(prompt1_c).mul(j * 1/(anim_args.interpolate_x_frames+1)))
                # sample the diffusion model
                results = generate(args, root)
                image = results[0]
                #Image callback mod
                if image_callback is not None:
                    image_callback(image)
                filename = f"{args.timestring}_{frame_idx:05}.png"
                image.save(os.path.join(args.outdir, filename))
                frame_idx += 1

                display.clear_output(wait=True)
                display.display(image)

                args.seed = next_seed(args)

    # generate the last prompt
    args.init_c = prompts_c_s[-1]
    results = generate(args, root)
    image = results[0]
    #Image callback mod
    if image_callback is not None:
        image_callback(image)
    filename = f"{args.timestring}_{frame_idx:05}.png"
    image.save(os.path.join(args.outdir, filename))

    display.clear_output(wait=True)
    display.display(image)
    args.seed = next_seed(args)

    #clear init_c
    args.init_c = None
