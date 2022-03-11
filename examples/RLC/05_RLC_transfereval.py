import os
from typing import Union
import numpy as np
import time
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
import torch
from torch import nn
import matplotlib.pyplot as plt

from torchid.statespace.module.ssmodels_ct import NeuralStateSpaceModel
from torchid.statespace.module.ss_simulator_ct import ForwardEulerSimulator
from diffutil.jacobian import parameter_jacobian
import loader
from diffutil.products import jvp, unflatten_like
from examples.RLC.utils import get_time_str
from torchid import metrics


class StateSpaceWrapper(torch.nn.Module):

    def __init__(self, model):
        super(StateSpaceWrapper, self).__init__()
        self.model = model

    def forward(self, u_in):
        x_0 = torch.zeros(2)
        x_sim_torch = self.model(x_0, u_in)
        y_out = x_sim_torch[:, [0]]
        return y_out

if __name__ == '__main__':

    time_start = time.time()

    # In[Set seed for reproducibility]
    np.random.seed(0)
    torch.manual_seed(0)

    # In[Settings]
    vectorize = True  # vectorize jacobian evaluation (experimental!)
    sigma = 0.1
    model_name = "ss_model"
    # ds_filename = 'transfereval/R:4.0_L:5e-05_C:3.5e-07.npy'
    ds_filename = 'val/R:1.0935361214295956_L:9.589787600234677e-05_C:2.9333830098482676e-07.npy'

    #! transfer
    # In[Load dataset]
    t, u, y, x = loader.rlc_loader_multitask(ds_filename,
                                             trajectory=0,
                                             steps=100,
                                             noise_std=sigma,
                                             scale=False)

    seq_len = t.size

    # In[Setup neural model structure and load fitted model parameters]
    ss_model = NeuralStateSpaceModel(n_x=2, n_u=1, n_feat=50)
    nn_solution = ForwardEulerSimulator(ss_model)
    model_filename = f"{model_name}.pt"
    nn_solution.ss_model.load_state_dict(
        torch.load(os.path.join("models", model_filename)))

    # In[Model wrapping]
    input_size = 1
    output_size = 1
    model_wrapped = StateSpaceWrapper(nn_solution)
    u_torch = torch.tensor(u[None, ...],
                           dtype=torch.float,
                           requires_grad=False)
    y_torch = torch.tensor(y[None, ...], dtype=torch.float)
    u_torch_f = torch.clone(u_torch.view(
        (1 * seq_len, input_size)))  # [bsize*seq_len, n_in] # [2000,1]
    y_torch_f = torch.clone(y_torch.view(1 * seq_len,
                                         output_size))  # [bsize*seq_len, ]
    # In[Adaptation in parameter space (naive way)]
    J = parameter_jacobian(
        model_wrapped, u_torch_f,
        vectorize=vectorize).detach().numpy()  # full parameter jacobian
    n_param = J.shape[1]
    Ip = np.eye(n_param)
    F = J.transpose() @ J
    A = F + sigma**2 * Ip
    theta_lin = np.linalg.solve(A, J.transpose() @ y)  # adaptation!

    adapt_time = time.time() - time_start
    print(f"\nAdapt time: {adapt_time:.2f}")

    #! eval
    t_new, u_new, y_new, x_new = loader.rlc_loader_multitask(ds_filename,
                                                             trajectory=1,
                                                             steps=2000,
                                                             noise_std=sigma,
                                                             scale=False)

    seq_len_new = t_new.size

    # In[Model wrapping]
    u_torch_new = torch.tensor(u_new[None, :, :])
    u_torch_new_f = torch.clone(u_torch_new.view(
        (1 * seq_len_new, input_size)))  # [bsize*seq_len, n_in]

    # In[Nominal model output]
    y_sim_new_f = model_wrapped(u_torch_new_f)
    y_sim_new = y_sim_new_f.reshape(seq_len_new, output_size).detach().numpy()

    # In[Parameter jacobian-vector product]
    theta_lin = torch.tensor(theta_lin)
    theta_lin_f = unflatten_like(theta_lin,
                                 tensor_lst=list(model_wrapped.parameters()))
    y_lin_new_f = jvp(y_sim_new_f, model_wrapped.parameters(), theta_lin_f)[0]
    y_lin_new = y_lin_new_f.reshape(seq_len_new, output_size).detach().numpy()

    # In[Plot]
    plt.plot(y_new, 'k', label="True")
    plt.plot(y_sim_new, 'r', label="Sim")
    plt.plot(y_lin_new, 'b', label="Lin-Sense")
    plt.legend()
    plt.grid()
    plt.xlim(0, 500)
    # plt.show()
    datetime_str = get_time_str()
    plt.savefig(f"fig/rlc_eval_results_{datetime_str}.pdf",
                bbox_inches='tight')

    # Saving state and input
    save_path = Path.cwd() / 'data' / 'RLC_SS_NL'
    save_path.mkdir(parents=True, exist_ok=True)
    np.save(str(save_path / "04_eval_y.npy"), y_new)
    np.save(str(save_path / "04_eval_y_sim.npy"), y_sim_new)
    np.save(str(save_path / "04_eval_y_lin.npy"), y_lin_new)

    # R-squared and MSE metrics
    R_sq = metrics.r_squared(y_new, y_lin_new)
    print(f"R-squared linear model: {R_sq}")

    R_sq = metrics.r_squared(y_new, y_sim_new)
    print(f"R-squared nominal model: {R_sq}")

    mse = np.mean((y_new - y_lin_new)**2, axis=0)
    print(f"MSE linear model: {mse}")
    mse = np.mean((y_new - y_sim_new)**2, axis=0)
    print(f"MSE nominal model: {mse}")