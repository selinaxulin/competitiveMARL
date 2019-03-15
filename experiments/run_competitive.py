import numpy as np
import torch
import time
import pickle
from copy import deepcopy

from rls import arglist
from rls.replay_buffer import ReplayBuffer
from rls.agent.multiagent.model_ddpg_competitive import Trainer


def split_own_adv(env, z):
    z_own = [y for x, y in zip(env.agents, z) if not x.adversary]
    z_adv = [y for x, y in zip(env.agents, z) if x.adversary]

    return z_own, z_adv


def combine_obs_n(obs_n_own, obs_n_adv):
    obs_n = obs_n_own + obs_n_adv
    return obs_n


def combine_action_n(action_n_own, action_n_adv):
    action_n_env = np.concatenate([action_n_adv, action_n_own], axis=0)
    return action_n_env


def run(env, actor_own, critic_own, actor_adv, critic_adv, 
        own_model_own=False, own_model_adv=False, adv_model_own=False, adv_model_adv=False,
        flag_train=True, scenario_name=None, action_type='Discrete', cnt=0):
    """
    function of learning agent
    """
    if flag_train:
        exploration_mode = 'train'
    else:
        exploration_mode = 'test'

    torch.set_default_tensor_type('torch.FloatTensor')
    print('observation shape: ', env.observation_space)
    print('action shape: ', env.action_space)

    # <create actor-critic networks>
    memory_own = ReplayBuffer(size=1e+6)
    memory_adv = ReplayBuffer(size=1e+6)
    learner_own = Trainer(actor_own, critic_own, memory_own, memory_adv,
                          model_own=own_model_own, model_adv=own_model_adv, action_type=action_type)
    learner_adv = Trainer(actor_adv, critic_adv, memory_adv, memory_own,
                          model_own=adv_model_own, model_adv=adv_model_adv, action_type=action_type)

    episode_rewards = [0.0]  # sum of rewards for all agents
    agent_rewards = [[0.0] for _ in range(env.n)]  # individual agent reward
    final_ep_rewards = []  # sum of rewards for training curve
    final_ep_ag_rewards = []  # agent rewards for training curve
    agent_info = [[[]]]  # placeholder for benchmarking info
    obs_n = env.reset()
    obs_n_own, obs_n_adv = split_own_adv(env, obs_n)
    episode_step = 0
    train_step = 0
    t_start = time.time()

    print('Starting iterations...')
    while True:
        # get action
        action_n_own = learner_own.get_exploration_action(obs_n_own, mode=exploration_mode)
        action_n_adv = learner_adv.get_exploration_action(obs_n_adv, mode=exploration_mode)

        # environment step
        action_n_env = combine_action_n(action_n_own, action_n_adv)
        new_obs_n, rew_n, done_n, info_n = env.step(action_n_env)
        new_obs_n_own, new_obs_n_adv = split_own_adv(env, new_obs_n)

        # make shared reward
        rew_own, rew_adv = split_own_adv(rew_n)
        rew_own = np.sum(rew_own)
        rew_adv = np.sum(rew_adv)

        episode_step += 1
        done = all(done_n)
        terminal = (episode_step >= arglist.max_episode_len)
        # collect experience
        learner_own.memory_own.add(obs_n_own, action_n_own, rew_own, new_obs_n_own, float(done))
        learner_adv.memory_own.add(obs_n_adv, action_n_adv, rew_adv, new_obs_n_adv, float(done))
        obs_n_own = new_obs_n_own
        obs_n_adv = new_obs_n_adv

        for i, rew in enumerate(rew_n):
            episode_rewards[-1] += rew
            agent_rewards[i][-1] += rew

        if done or terminal:
            obs_n = env.reset()
            obs_n_own, obs_n_adv = split_own_adv(env, obs_n)
            episode_step = 0
            episode_rewards.append(0)
            for a in agent_rewards:
                a.append(0)
            agent_info.append([[]])

        # increment global step counter
        train_step += 1

        # for displaying learned policies
        if arglist.display:
            time.sleep(0.1)
            env.render()
            continue

        # update all trainers, if not in display or benchmark mode
        # <learning agent>
        do_learn = (train_step > arglist.warmup_steps) and (
                train_step % arglist.update_rate == 0) and arglist.is_training
        if flag_train and do_learn:
            loss_own = learner_own.optimize()
            loss_adv = learner_adv.optimize()

        if flag_train:
            # save model, display training output
            if terminal and (len(episode_rewards) % arglist.save_rate == 0):
                # print statement depends on whether or not there are adversaries
                print("steps: {}, episodes: {}, mean episode reward: {}, time: {}".format(
                    train_step, len(episode_rewards), np.mean(episode_rewards[-arglist.save_rate:]),
                    round(time.time() - t_start, 3)))
                t_start = time.time()
                # Keep track of final episode reward
                final_ep_rewards.append(np.mean(episode_rewards[-arglist.save_rate:]))
                for rew in agent_rewards:
                    final_ep_ag_rewards.append(np.mean(rew[-arglist.save_rate:]))

            # saves final episode reward for plotting training curve later
            if len(episode_rewards) > arglist.num_episodes:
                hist = {'reward_episodes': episode_rewards, 'reward_episodes_by_agents': agent_rewards}
                file_name = 'Models/history_' + scenario_name + '_' + str(cnt) + '.pkl'
                with open(file_name, 'wb') as fp:
                    pickle.dump(hist, fp)
                print('...Finished total of {} episodes.'.format(len(episode_rewards)))
                # save model
                learner_own.save_models(scenario_name + 'own_fin_' + str(cnt))
                learner_adv.save_models(scenario_name + 'adv_fin_' + str(cnt))
                break
        else:
            # save model, display testing output
            if terminal and (len(episode_rewards) % 10 == 0):
                # print statement depends on whether or not there are adversaries
                print("steps: {}, episodes: {}, mean episode reward: {}, time: {}".format(
                    train_step, len(episode_rewards), np.mean(episode_rewards[-10:]),
                    round(time.time() - t_start, 3)))
                t_start = time.time()
                # Keep track of final episode reward
                final_ep_rewards.append(np.mean(episode_rewards[-10:]))
                for rew in agent_rewards:
                    final_ep_ag_rewards.append(np.mean(rew[-10:]))

            # saves final episode reward for plotting training curve later
            if len(episode_rewards) > arglist.num_episodes:
                hist = {'reward_episodes': episode_rewards,
                        'reward_episodes_by_agents': agent_rewards}
                file_name = 'Models/test_history_' + scenario_name + '_' + str(cnt) + '.pkl'
                with open(file_name, 'wb') as fp:
                    pickle.dump(hist, fp)
                print('...Finished total of {} episodes.'.format(len(episode_rewards)))
                break




if __name__ == '__main__':
    import torch
    import numpy as np
    from experiments.scenarios import make_env
    from rls import arglist

    # proposed
    from rls.model.ac_networks_competitive import ActorNetwork, CriticNetwork
    from experiments.run_competitive import run

    import os

    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"  # see issue #152
    os.environ["CUDA_VISIBLE_DEVICES"] = '0'


    TEST_ONLY = False
    arglist.actor_learning_rate = 1e-2
    arglist.critic_learning_rate = 1e-2

    scenario_name = 'simple_adversary'
    env = make_env(scenario_name, discrete_action=True)
    cnt = 0
    seed = cnt + 12345678

    env.seed(seed)
    torch.cuda.empty_cache()
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    dim_obs_own = env.observation_space[-1].shape[0]
    dim_obs_adv = env.observation_space[0].shape[0]
    dim_action = env.action_space[0].n
    action_type = 'Discrete'
    # own
    actor_own = ActorNetwork(input_dim=dim_obs_own, out_dim=dim_action,
                             model_own=True, model_adv=True)
    critic_own = CriticNetwork(input_dim=dim_obs_own + np.sum(dim_action), out_dim=1,
                               model_own=True, model_adv=True)
    # opponent
    actor_adv = ActorNetwork(input_dim=dim_obs_adv, out_dim=dim_action,
                             model_own=True, model_adv=True)
    critic_adv = CriticNetwork(input_dim=dim_obs_adv + np.sum(dim_action), out_dim=1,
                               model_own=True, model_adv=True)

    if TEST_ONLY:
        arglist.num_episodes = 100
        flag_train = False
    else:
        arglist.num_episodes = 40000
        flag_train = True

    run(env, actor_own, critic_own, actor_adv, critic_adv,
        own_model_own=True, own_model_adv=True, adv_model_own=True, adv_model_adv=True,
        flag_train=flag_train, scenario_name=scenario_name,
        action_type='Discrete', cnt=0)



