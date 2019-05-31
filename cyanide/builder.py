from collections import defaultdict

import numpy as np

from .mof import MOF
from .scaler import Scaler
from .locator import Locator
from .local_structure import LocalStructure

# bb: building block.
class Builder:
    def __init__(self):
        self.scaler = Scaler()
        self.locator = Locator()

    def build(self, topology, node_bbs, edge_bbs=None, verbose=False):
        """
        The node_bbs must be given with proper order.
        Same as node type order in topology.
        """
        if edge_bbs is None:
            edge_bbs = defaultdict(lambda: None)
        else:
            edge_bbs = defaultdict(lambda: None, edge_bbs)

        if verbose:
            echo = print
        else:
            echo = lambda x: None

        assert topology.n_node_types == len(node_bbs)

        # Calculate bonds before start.
        echo("Calculating bonds in building blocks...")
        for node in node_bbs:
            node.bonds

        for edge in edge_bbs.values():
            if edge is None:
                continue
            edge.bonds

        echo("Scale topology...")
        # Get scaled topology.
        scaled_topology = self.scaler.scale(topology, node_bbs, edge_bbs)

        # Replace topology to scaled_topology
        topology = scaled_topology

        # Locate nodes and edges.
        located_bbs = [None for _ in range(topology.n_all_points)]

        echo("Placing nodes...")
        # Locate nodes.
        for t, node_bb in enumerate(node_bbs):
            # t: node type.
            for i in topology.node_indices:
                if t != topology.get_node_type(i):
                    continue
                target = topology.local_structure(i)
                located_node, rms = self.locator.locate(target, node_bb)
                # Translate.
                center = topology.atoms[i].position
                located_node.set_center(center)
                located_bbs[i] = located_node
                echo("Node {} is located, RMSD: {:.2E}".format(i, rms))

        # Calculate matching permutations of nodes.
        # Permutation of edges are matched later.
        permutations = [None for _ in range(topology.n_all_points)]
        for i in topology.node_indices:
            bb = located_bbs[i]
            local_topo = topology.local_structure(i)
            local_bb = bb.local_structure()

            # pos of local_topo.indices ~ pos of local_bb.indices[perm]
            perm = local_topo.matching_permutation(local_bb)
            permutations[i] = perm

        def find_matched_atom_indices(e):
            """
            Inputs:
                e: Edge index.

            External variables:
                topology, located_bbs, permutations.
            """
            # i and j: edge index in topology
            n1, n2 = topology.neighbor_list[e]

            i1 = n1.index
            i2 = n2.index

            bb1 = located_bbs[i1]
            bb2 = located_bbs[i2]

            # Find bonded atom index for i1.
            for o, n in enumerate(topology.neighbor_list[i1]):
                # Check zero sum.
                s = n.distance_vector + n1.distance_vector
                s = np.linalg.norm(s)
                if s < 1e-3:
                    perm = permutations[i1]
                    a1 = bb1.connection_point_indices[perm][o]
                    break

            # Find bonded atom index for i2.
            for o, n in enumerate(topology.neighbor_list[i2]):
                # Check zero sum.
                s = n.distance_vector + n2.distance_vector
                s = np.linalg.norm(s)
                if s < 1e-3:
                    perm = permutations[i2]
                    a2 = bb2.connection_point_indices[perm][o]
                    break

            return a1, a2

        # Locate edges.
        c = topology.atoms.cell
        invc = np.linalg.inv(topology.atoms.cell)
        for t, edge_bb in edge_bbs.items():
            if edge_bb is None:
                continue
            for e in topology.edge_indices:
                ti, tj = topology.get_edge_type(e)
                if t != (ti, tj):
                    continue

                n1, n2 = topology.neighbor_list[e]

                i1 = n1.index
                i2 = n2.index

                bb1 = located_bbs[i1]
                bb2 = located_bbs[i2]

                a1, a2 = find_matched_atom_indices(e)

                r1 = bb1.atoms.positions[a1]
                r2 = bb2.atoms.positions[a2]

                d = r2 - r1

                # Apply simple minimum image convection.
                s = d @ invc
                s = np.where(s>0.5, s-1.0, s)
                s = np.where(s<-0.5, s+1.0, s)
                d = s @ c

                center = r1 + 0.5*d

                target = LocalStructure(np.array([r1, r1+d]), [i1, i2])
                located_edge, rms = self.locator.locate(target, edge_bb)

                located_edge.set_center(center)
                located_bbs[e] = located_edge

                echo("Edge {} is located, RMSD: {:.2E}".format(i, rms))

        # Calculate edge matching permutations
        for i in topology.edge_indices:
            bb = located_bbs[i]
            if bb is None:
                continue
            local_topo = topology.local_structure(i)
            local_bb = bb.local_structure()

            # pos of local_topo.indices ~ pos of local_bb.indices[perm]
            perm = local_topo.matching_permutation(local_bb)
            permutations[i] = perm

        echo("Finding bonds in generated MOF...")
        echo("Finding bonds in building blocks...")
        # Build bonds of generated MOF.
        index_offsets = [None for _ in range(topology.n_all_points)]
        index_offsets[0] = 0
        for i, bb in enumerate(located_bbs[:-1]):
            if bb is None:
                index_offsets[i+1] = index_offsets[i] + 0
            else:
                index_offsets[i+1] = index_offsets[i] + bb.n_atoms

        bb_bonds = []
        for offset, bb in zip(index_offsets, located_bbs):
            if bb is None:
                continue
            bb_bonds.append(bb.bonds + offset)
        bb_bonds = np.concatenate(bb_bonds, axis=0)

        echo("Finding bonds between building blocks...")

        # Find bond between building blocks.
        bonds = []
        for j in topology.edge_indices:
            a1, a2 = find_matched_atom_indices(j)

            # i and j: edge index in topology
            n1, n2 = topology.neighbor_list[j]
            i1 = n1.index
            i2 = n2.index
            a1 += index_offsets[i1]
            a2 += index_offsets[i2]

            # Edge exists.
            if located_bbs[j] is not None:
                perm = permutations[j]
                e1, e2 = (
                    located_bbs[j].connection_point_indices[perm]
                    + index_offsets[j]
                )
                bonds.append((e1, a1))
                bonds.append((e2, a2))
            else:
                bonds.append((a1, a2))

            echo("Bonds on topology edge {} are connected.".format(j))

        bonds = np.array(bonds)

        # All bonds in generated MOF.
        all_bonds = np.concatenate([bb_bonds, bonds], axis=0)

        echo("Making MOF instance...")
        # Make full atoms from located building blocks.
        bb_atoms_list = [v.atoms for v in located_bbs if v is not None]

        mof_atoms = sum(bb_atoms_list[1:], bb_atoms_list[0])
        mof_atoms.set_pbc(True)
        mof_atoms.set_cell(topology.atoms.cell)

        mof = MOF(mof_atoms, all_bonds, wrap=True)
        echo("Done.")

        return mof
