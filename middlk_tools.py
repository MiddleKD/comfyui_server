import os
import aiohttp
from ai_api import get_mime_type_from_binary

import json

def log_parameters_to_json(file_name="parameters.json"):
    def convert_value_to_str(value):
        if hasattr(value, "__dict__"):
            return {attr: str(getattr(value, attr)) for attr in value.__dict__}
        else:
            return str(value)
    
    # Retrieve the local variables from the calling function's scope
    import inspect
    frame = inspect.currentframe().f_back
    local_vars = frame.f_locals

    # Create a list to hold the detailed parameter information
    detailed_params = []

    # Print each parameter and its type, and collect the detailed information
    for key, value in local_vars.items():
        detail = {
            "name": key,
            "value": convert_value_to_str(value),
            "type": type(value).__name__
        }
        detailed_params.append(detail)
        print(f"{key}: {value}, type: {type(value)}")
    
    # Save detailed parameter information to a JSON file
    with open(file_name, 'w') as f:
        json.dump(detailed_params, f, indent=4)

import json
def save_json_to_check(json_like, file_name="tocheck.json"):
    with open(file_name, mode="a") as f:
        json_str = json.dumps(json_like, indent=4)
        f.write(json_str + "\n")
    return True


import tracemalloc
def trace_memory():
    # 메모리 추적 시작
    tracemalloc.start()

    # 코드 실행 (예시)
    x = [i for i in range(1000000)]  # 큰 리스트 생성

    # 현재 메모리 스냅샷 생성
    snapshot = tracemalloc.take_snapshot()

    # 가장 많은 메모리를 사용하고 있는 top 10 개의 객체 출력
    top_stats = snapshot.statistics('lineno')

    print("[ Top 10 ]")
    for stat in top_stats[:10]:
        print(stat)

import matplotlib.pyplot as plt
import numpy as np
import torch
def visualize_tensor(tensor):
    """
    1, 4, 64, 64 모양의 텐서를 시각화합니다.
    텐서 전체의 통계치를 출력하고 64x256 크기의 이미지를 시각화합니다.

    Args:
        tensor (torch.Tensor): 1, 4, 64, 64 모양의 텐서.
    """
    # 텐서가 1, 4, 64, 64 모양인지 확인
    assert tensor.shape == (1, 4, 64, 64), "Tensor shape must be (1, 4, 64, 64)"
    
    # 배치 차원을 제거하여 4, 64, 64 모양으로 변환
    tensor = tensor.squeeze(0)

    # 텐서 전체의 통계치 계산
    max_val = tensor.max().item()
    min_val = tensor.min().item()
    mean_val = tensor.mean().item()
    std_val = tensor.std().item()
    median_val = torch.median(tensor).item()
    
    # 통계치 출력
    print("Overall Tensor Statistics:")
    print(f"Max: {max_val}")
    print(f"Min: {min_val}")
    print(f"Mean: {mean_val}")
    print(f"Std: {std_val}")
    print(f"Median: {median_val}\n")
    
    # 각 채널을 개별적으로 시각화
    fig, ax = plt.subplots(figsize=(12, 5))

    # 64x256 크기의 빈 캔버스 생성
    combined_image = np.zeros((64, 256))

    for i in range(4):
        channel = tensor[i].numpy()
        
        # 각 채널의 값을 0과 1 사이로 정규화
        channel = channel / max_val if max_val != 0 else channel
        
        # 64x64 이미지를 64x256 이미지의 올바른 위치에 삽입
        combined_image[:, i*64:(i+1)*64] = channel
    
    # 64x256 이미지를 시각화
    ax.imshow(combined_image, cmap='gray')
    ax.set_title('Combined Channels')
    ax.axis('off')

    # 통계치 텍스트 추가
    stats_text = (
        f"Overall Tensor Statistics:\n"
        f"Max: {max_val:.2f}, Min: {min_val:.2f}, Mean: {mean_val:.2f}, "
        f"Std: {std_val:.2f}, Median: {median_val:.2f}"
    )
    plt.figtext(0.5, -0.1, stats_text, wrap=True, horizontalalignment='center', fontsize=12)

    import uuid
    plt.savefig(f"{uuid.uuid4()}")

def scatter_plot_tensor(tensor):
    """
    1, 4, 64, 64 모양의 텐서를 scatter plot으로 시각화합니다.
    텐서 전체의 통계치를 출력하고 scatter plot 이미지를 저장합니다.

    Args:
        tensor (torch.Tensor): 1, 4, 64, 64 모양의 텐서.
        file_path (str): 이미지 파일 경로.
    """
    # 텐서가 1, 4, 64, 64 모양인지 확인
    assert tensor.shape == (1, 4, 64, 64), "Tensor shape must be (1, 4, 64, 64)"
    
    # 배치 차원을 제거하여 4, 64, 64 모양으로 변환
    tensor = tensor.squeeze(0)

    # 텐서 전체의 통계치 계산
    max_val = tensor.max().item()
    min_val = tensor.min().item()
    mean_val = tensor.mean().item()
    std_val = tensor.std().item()
    median_val = torch.median(tensor).item()
    
    # 통계치 출력
    print("Overall Tensor Statistics:")
    print(f"Max: {max_val}")
    print(f"Min: {min_val}")
    print(f"Mean: {mean_val}")
    print(f"Std: {std_val}")
    print(f"Median: {median_val}\n")
    
    # 각 채널을 scatter plot으로 시각화
    fig, axs = plt.subplots(2, 2, figsize=(12, 10))
    axs = axs.flatten()

    for i in range(4):
        channel = tensor[i].numpy()
        x_coords, y_coords = np.meshgrid(np.arange(64), np.arange(64))
        x_coords = x_coords.flatten()
        y_coords = y_coords.flatten()
        values = channel.flatten()
        
        # scatter plot 그리기
        sc = axs[i].scatter(x_coords, y_coords, c=values, cmap='viridis', alpha=0.6)
        axs[i].set_title(f'Channel {i+1}')
        axs[i].set_xlabel('X Coordinate')
        axs[i].set_ylabel('Y Coordinate')
        plt.colorbar(sc, ax=axs[i], orientation='vertical')

    plt.tight_layout()
    import uuid
    plt.savefig(f"{uuid.uuid4()}")